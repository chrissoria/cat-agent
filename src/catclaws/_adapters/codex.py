"""Codex adapter — openai-codex SDK implementation of AgentAdapter.

Requires a ChatGPT-plan ``codex login`` on this machine (the SDK bundles the
codex binary via `openai-codex-cli-bin` and reuses the CLI's stored
credentials — verified live 2026-07-11: billing rides the plan even with
OPENAI_API_KEY in the environment). Calls run through the user's ChatGPT
subscription, not an API key.

Every fact this module codes against was probed live on openai-codex 0.1.0b3
(bundled codex-cli 0.137.0-alpha.4) — see OPENAI_MASTERPLAN.md §3 for the
recorded findings, especially:

- Sealed session = `Sandbox.read_only` + `ApprovalMode.deny_all` + an EMPTY
  tempdir `cwd` (AGENTS.md in the cwd IS injected otherwise — the cwd is the
  codex analog of Claude's `setting_sources=[]` contamination vector) +
  `ephemeral=True` (no thread persistence) + `base_instructions`, which
  REPLACES the default persona.
- `effort="none"` yields reasoning_output_tokens=0 and beats the user's
  ~/.codex/config.toml global (`model_reasoning_effort = "xhigh"` on the
  reference machine). Never send effort=None: that silently inherits user
  config — slow, quota-burning, non-reproducible.
- `TurnResult.status` is a plain enum: compare `.value`, never the string.
- Images (spike P9): the SDK takes image FILE PATHS
  (`[TextInput(text=...), LocalImageInput(path=...)]`, verified on an 8x8
  PNG), not the adapter contract's base64. `one_shot` writes each image to a
  private tempdir -- kept OUT of the sealed empty `cwd` -- and deletes it
  after the call.
"""

import base64
import binascii
import inspect
import os
import shutil
import tempfile

from .base import (
    RATE_LIMIT_PREFIX,
    AgentAdapter,
    _finalize,
    _looks_rate_limited_text,
)


def _rate_limit_detail_from_turn_error(err) -> str | None:
    """Typed rate-limit detection on a failed turn's ``TurnError``.

    The SDK's `CodexErrorInfo.root` enum carries `'usageLimitExceeded'` for a
    hit ChatGPT-plan window (5-hour / weekly). Normalize and substring-match
    so sibling variants (`rate_limit_reached`, credit-depletion codes) map
    too. Falls back to rate-limit wording in the error message. Reset times
    are not exposed on the TurnError in 0.1.0b3, so no ``resets at epoch``
    suffix — classify() then uses its bounded backoff (the contract's
    documented fallback for adapters without a reset time).
    """
    if err is None:
        return None
    info = getattr(err, "codex_error_info", None)
    root = getattr(info, "root", None)
    root_val = getattr(root, "value", root)
    if isinstance(root_val, str):
        squashed = root_val.lower().replace("_", "").replace("-", "")
        if "usagelimit" in squashed or "ratelimit" in squashed or "creditsdepleted" in squashed:
            msg = getattr(err, "message", None)
            return f"{root_val}" + (f": {msg}" if msg else "")
    msg = getattr(err, "message", None)
    if msg and _looks_rate_limited_text(msg):
        return str(msg)
    return None


_IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _write_images(images, directory):
    """Decode the contract's ``{"media_type", "data"}`` images into files in
    `directory`; return their paths in order. Raises ValueError on bad data."""
    paths = []
    for i, im in enumerate(images):
        ext = _IMAGE_EXTENSIONS.get((im.get("media_type") or "image/png").lower(), "png")
        try:
            raw = base64.b64decode(im["data"], validate=True)
        except (KeyError, binascii.Error, TypeError) as e:
            raise ValueError(f"image {i} is not valid base64 data ({e})") from e
        path = os.path.join(directory, f"image_{i}.{ext}")
        with open(path, "wb") as f:
            f.write(raw)
        paths.append(path)
    return paths


class CodexAdapter(AgentAdapter):
    name = "codex"
    default_model = "gpt-5.5"

    async def one_shot(
        self,
        prompt: str,
        system_prompt: str | None,
        model: str,
        thinking_budget: int = 0,
        images: list | None = None,
    ) -> tuple[str | None, str | None]:
        try:
            from openai_codex import ApprovalMode, AsyncCodex, Sandbox
        except ImportError as e:
            return None, (
                'openai-codex is not installed. Run: pip install "cat-claws[codex]" '
                f"(original error: {e})"
            )

        # Images -> files for LocalImageInput (the SDK takes paths). Written
        # to their own tempdir, never the sealed empty cwd, and removed in
        # the finally below.
        image_dir = None
        run_input = prompt
        if images:
            from openai_codex import LocalImageInput, TextInput
            image_dir = tempfile.mkdtemp(prefix="catclaws-codex-img-")
            try:
                paths = _write_images(images, image_dir)
            except ValueError as e:
                shutil.rmtree(image_dir, ignore_errors=True)
                return None, f"codex adapter: {e}"
            run_input = [TextInput(text=prompt)] + [LocalImageInput(path=p) for p in paths]

        # Engine parity: thinking_budget=0 -> reasoning off. "none" is
        # accepted live (0 reasoning tokens) even though models() only
        # advertises low..xhigh; if a model rejects it, the fallback below
        # retries at "low" (the universally advertised minimum) rather than
        # effort=None, which would inherit the user's config.toml global.
        if thinking_budget and thinking_budget > 0:
            from catstack._providers import _thinking_budget_to_effort
            effort = _thinking_budget_to_effort(thinking_budget)  # low|medium|high
        else:
            effort = "none"

        async def _run(effort_arg):
            """One sealed thread + one turn -> (text, result_error, rate_limit_detail)."""
            scratch = tempfile.mkdtemp(prefix="catclaws-codex-")
            async with AsyncCodex() as codex:
                thread_kwargs = dict(
                    model=model,
                    cwd=scratch,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    ephemeral=True,
                )
                if system_prompt:
                    thread_kwargs["base_instructions"] = system_prompt
                # On AsyncCodex, thread_start is a coroutine (verified live —
                # the sync client's is a plain method; inspect.signature hides
                # the difference). The guard tolerates either.
                thread = codex.thread_start(**thread_kwargs)
                if inspect.iscoroutine(thread):
                    thread = await thread
                result = await thread.run(run_input, effort=effort_arg)

            status = getattr(result.status, "value", result.status)
            if status == "completed":
                return (result.final_response or "").strip(), None, None

            err = getattr(result, "error", None)
            detail = _rate_limit_detail_from_turn_error(err)
            if detail:
                return "", None, detail
            msg = getattr(err, "message", None) or f"turn {status}"
            extra = getattr(err, "additional_details", None)
            return "", (f"{msg} ({extra})" if extra else str(msg)), None

        try:
            return await self._run_with_fallback(_run, effort)
        finally:
            if image_dir:
                shutil.rmtree(image_dir, ignore_errors=True)

    async def _run_with_fallback(self, _run, effort):
        """Run once; on an effort rejection retry at "low" (never None)."""
        try:
            return _finalize(*await _run(effort))
        except Exception as e:
            if _looks_rate_limited_text(e):
                return None, f"{RATE_LIMIT_PREFIX}{e}"
            # Effort incompatibilities (a model/account that rejects "none")
            # retry once at the lowest advertised tier rather than failing
            # the row — mirrors claude.py's thinking fallback. Never fall
            # back to effort=None (inherits user config, see module doc).
            if ("effort" in str(e).lower() or "reasoning" in str(e).lower()) and effort == "none":
                try:
                    return _finalize(*await _run("low"))
                except Exception as e2:
                    if _looks_rate_limited_text(e2):
                        return None, f"{RATE_LIMIT_PREFIX}{e2}"
                    return None, f"codex adapter failed: {e2}"
            low = str(e).lower()
            if "login" in low or "auth" in low or "unauthorized" in low:
                return None, (
                    "codex is not logged in. Run: codex login "
                    f"(requires a ChatGPT plan; original error: {e})"
                )
            return None, f"codex adapter failed: {e}"
