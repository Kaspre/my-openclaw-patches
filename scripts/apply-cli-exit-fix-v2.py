#!/usr/bin/env python3
"""Minimal CLI exit fix — bounds `openclaw agent --local` lifetime.

Issue: #63609 — CLI commands hang indefinitely after completing.
Files: dist/index.js, dist/entry.js (only 2 files, no internal patches).

Two mechanisms:

  Post-resolve forced exit: when the CLI promise resolves, call process.exit()
  with a 3s SIGKILL fallback. Handles the common case where work completes but
  ref'd handles (WebSocket, LCM, otel) prevent natural exit.

  Wall-clock safety net (agent --local only): synchronous IIFE armed before
  CLI entry. Parses --timeout from argv (no config import, no async). Falls
  back to 600s. Exits 124 after timeout + 30s grace. The 30s grace lets OC's
  own --timeout fire first for clean shutdown.

Replaces the 724-LOC apply-cli-exit-fix.py. Drops all context-engine /
run-main.js / maintenance-bundle patches — those are OC's responsibility.
"""
import argparse
import re
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"

APPLIED_MARKER = "local agent command timed out after"
OLD_SIGKILL_MARKER = "cli-exit-fix: hard wall-clock SIGKILL"


def _hard_timer_iife(proc: str) -> str:
    """Synchronous IIFE — no async, no imports, no config read."""
    return (
        "(()=>{"
        f"const a={proc}.argv;"
        "let sub;for(let i=2;i<a.length;i++){const v=a[i];if(!v)continue;if(v==='--')break;if(!v.startsWith('-')){sub=v;break;}}"
        "if(sub!=='agent')return;"
        "let hasLocal=false,s;"
        "for(let i=2;i<a.length;i++){const v=a[i];if(!v)continue;if(v==='--')break;"
        "if(v==='-h'||v==='--help'||v==='--version'||v==='-V')return;"
        "if(v==='--local'||v.startsWith('--local='))hasLocal=true;"
        "if(v==='--timeout')s=a[i+1];else if(v.startsWith('--timeout='))s=v.slice(10);}"
        "if(!hasLocal)return;"
        "let n=600;"
        "if(s!==undefined){const p=parseInt(s,10);if(Number.isFinite(p)&&p>=0)n=p;}"
        "if(n===0)return;"
        "const ms=(n+30)*1000;"
        f"const t=setTimeout(()=>{{try{{{proc}.stderr.write(`local agent command timed out after ${{n}}s plus 30s grace\\n`);}}catch{{}}"
        f"try{{{proc}.exit(124);}}catch{{}}}},ms);"
        "t.unref&&t.unref();"
        "})()"
    )


def _forced_exit(proc: str) -> str:
    return (
        f"setTimeout(()=>{{try{{{proc}.kill({proc}.pid,'SIGKILL');}}catch{{}}}},3000);"
        f"{proc}.exit({proc}.exitCode??0);"
    )


RECIPES = [
    {
        "file": "index.js",
        "desc": "index.js: post-resolve exit + wall-clock timeout",
        # Unpatched: runLegacyCliEntry(process.argv).catch(
        "anchor_re": re.compile(
            r"runLegacyCliEntry\((process(?:\$\d+)?)\.argv\)\.catch\("
        ),
        "build": lambda proc: (
            f"{_hard_timer_iife(proc)};"
            f"runLegacyCliEntry({proc}.argv)"
            f".then(()=>{{{_forced_exit(proc)}}})"
            ".catch("
        ),
    },
    {
        "file": "entry.js",
        "desc": "entry.js: post-resolve exit + wall-clock timeout",
        # Unpatched: if (!tryHandleRootVersionFastPath(process$1.argv)) await runMainOrRootHelp(process$1.argv);
        "anchor_re": re.compile(
            r"if \(!tryHandleRootVersionFastPath\((process(?:\$\d+)?)\.argv\)\) "
            r"await runMainOrRootHelp\(\1\.argv\);"
        ),
        "build": lambda proc: (
            f"{_hard_timer_iife(proc)};"
            f"if (!tryHandleRootVersionFastPath({proc}.argv)) "
            f"{{ await runMainOrRootHelp({proc}.argv); {_forced_exit(proc)} }}"
        ),
    },
]

# Patterns that match any prior version of our patch (v1 old SIGKILL, v1 async, v2)
PRIOR_PATCH_RES = [
    # v1 old-style: (() => { ... SIGKILL ... })(); runLegacyCliEntry(...).then(...)
    re.compile(
        r"\(\(\)=>\{try\{const a=(process(?:\$\d+)?)\.argv;[\s\S]*?cli-exit-fix: hard wall-clock SIGKILL[\s\S]*?\}\)\(\);"
        r"runLegacyCliEntry\(\1\.argv\)\.then\(\(\) => \{ setTimeout\(\(\) => \{ try \{ \1\.kill\(\1\.pid, 'SIGKILL'\); \} catch \{\} \}, 3000\); \1\.exit\(\1\.exitCode \?\? 0\); \}\)\.catch\("
    ),
    # v1 config-backed: const __ocCliHardTimeout=await (async()=>{ ... })(); ... .then(...)
    re.compile(
        r"const __ocCliHardTimeout=(?:await )?(?:\(async\(\)|\(\(\))[\s\S]*?local agent command timed out after[\s\S]*?\}\)\(\);"
        r"runLegacyCliEntry\((process(?:\$\d+)?)\.argv\)\.then\(\(\) => \{ try \{ __ocCliHardTimeout&&__ocCliHardTimeout\(\); \} catch \{\} setTimeout\(\(\) => \{ try \{ \1\.kill\(\1\.pid, 'SIGKILL'\); \} catch \{\} \}, 3000\); \1\.exit\(\1\.exitCode \?\? 0\); \}\)\.catch\("
    ),
    # v1 config-backed entry.js with forced-exit: ... if (!tryHandle...) { try { await runMain... } finally { ... } setTimeout... }
    re.compile(
        r"const __ocCliHardTimeout=(?:await )?(?:\(async\(\)|\(\(\))[\s\S]*?local agent command timed out after[\s\S]*?\}\)\(\);"
        r"if \(!tryHandleRootVersionFastPath\((process(?:\$\d+)?)\.argv\)\) \{ try \{ await runMainOrRootHelp\(\1\.argv\); \} finally \{ try \{ __ocCliHardTimeout&&__ocCliHardTimeout\(\); \} catch \{\} \} setTimeout\(\(\) => \{ try \{ \1\.kill\(\1\.pid, 'SIGKILL'\); \} catch \{\} \}, 3000\); \1\.exit\(\1\.exitCode \?\? 0\); \}"
    ),
    # v1 config-backed entry.js without forced-exit: ... if (!tryHandle...) { try { await runMain... } finally { ... } }
    re.compile(
        r"const __ocCliHardTimeout=(?:await )?(?:\(async\(\)|\(\(\))[\s\S]*?local agent command timed out after[\s\S]*?\}\)\(\);"
        r"if \(!tryHandleRootVersionFastPath\((process(?:\$\d+)?)\.argv\)\) \{ try \{ await runMainOrRootHelp\(\1\.argv\); \} finally \{ try \{ __ocCliHardTimeout&&__ocCliHardTimeout\(\); \} catch \{\} \} \}"
    ),
    # v2 (this script): (()=>{ ... })(); runLegacyCliEntry(...).then(...)
    re.compile(
        r"\(\(\)=>\{const a=(process(?:\$\d+)?)\.argv;[\s\S]*?local agent command timed out after[\s\S]*?\}\)\(\);"
        r"runLegacyCliEntry\(\1\.argv\)\.then\(\(\)=>\{[\s\S]*?\}\)\.catch\("
    ),
    # v2 (this script) entry.js: (()=>{ ... })(); if (!tryHandle...) { await runMain...; ... }
    re.compile(
        r"\(\(\)=>\{const a=(process(?:\$\d+)?)\.argv;[\s\S]*?local agent command timed out after[\s\S]*?\}\)\(\);"
        r"if \(!tryHandleRootVersionFastPath\(\1\.argv\)\) \{ await runMainOrRootHelp\(\1\.argv\); [\s\S]*?\}"
    ),
]


def _find_prior_patch(content: str) -> tuple[re.Match | None, str]:
    """Find and return the match for any prior patch version, plus its alias."""
    for pat in PRIOR_PATCH_RES:
        matches = list(pat.finditer(content))
        if len(matches) == 1:
            return matches[0], matches[0].group(1)
    return None, ""


def main():
    parser = argparse.ArgumentParser(description="Minimal CLI exit fix (v2)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    dist = args.dist_dir
    if not dist.exists():
        print(f"SKIP: dist dir not found: {dist}")
        sys.exit(0)

    all_ok = True
    for recipe in RECIPES:
        fpath = dist / recipe["file"]
        if not fpath.exists():
            print(f"SKIP: {recipe['file']} not found")
            continue

        content = fpath.read_text()

        # Already applied (v2)?
        if APPLIED_MARKER in content and "readBestEffortConfig" not in content and OLD_SIGKILL_MARKER not in content:
            print(f"OK: {recipe['desc']} (already applied)")
            continue

        # Try to match a prior patch version to replace
        prior, alias = _find_prior_patch(content)
        if prior:
            replacement = recipe["build"](alias)
            if args.dry_run:
                print(f"DRY-RUN: would replace prior patch in {recipe['desc']} (alias={alias})")
                continue
            new_content = content[:prior.start()] + replacement + content[prior.end():]
            fpath.write_text(new_content)
            print(f"APPLIED: {recipe['desc']} (replaced prior patch, alias={alias})")
            continue

        # Fresh apply against unpatched code
        matches = list(recipe["anchor_re"].finditer(content))
        if not matches:
            print(f"WARN: {recipe['desc']} — anchor not found (structural drift)")
            all_ok = False
            continue
        if len(matches) > 1:
            print(f"WARN: {recipe['desc']} — anchor matched {len(matches)}x (expected 1)")
            all_ok = False
            continue

        match = matches[0]
        alias = match.group(1)
        replacement = recipe["build"](alias)

        if args.dry_run:
            print(f"DRY-RUN: would apply {recipe['desc']} (alias={alias})")
            continue

        new_content = content.replace(match.group(0), replacement, 1)
        fpath.write_text(new_content)
        print(f"APPLIED: {recipe['desc']} (alias={alias})")

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
