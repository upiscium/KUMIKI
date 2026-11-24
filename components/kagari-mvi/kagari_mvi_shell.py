"""Reading a Bash command as syntax, for the permission ledger (#430, ADR-0010 D2).

Only a plain `word word ...` command is understood. A compound command (`&&`, `;`, `|`, `&`,
redirects, subshells, substitutions, newlines) or one that cannot be parsed gives None, and the
ledger never allows it automatically. Per ADR-0009 D1 this guards against everyday mistakes, not
against a deliberate way around it.
"""
from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional, Tuple

# Always denied, whatever the ledger says (no grant can allow them for now): a speed bump, not a boundary
# (python3 -c can still reach the network).
DENIED_COMMANDS = ("curl", "wget", "gh", "git push", "git remote", "ssh", "scp", "sudo", "su",
                   "nc", "ncat", "socat", "telnet")
OPERATORS = set("();<>|&")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "builtin"}
# Wrapper -> (short options with a value, short flags, long options with a value, long flags). An option not
# listed makes the whole rest of the words be scanned for a denied command (the safe side).
WRAPPERS = {
    "command": ("", "pvV", (), ()), "time": ("fo", "pvalV", ("--format", "--output"), ("--portability", "--verbose",
                                                                                         "--append", "--quiet")),
    "exec": ("a", "cl", (), ()), "nohup": ("", "", (), ()), "setsid": ("", "cfw", (), ("--fork", "--wait", "--ctty")),
    "env": ("uCS", "i0v", ("--unset", "--chdir", "--split-string"), ("--ignore-environment", "--null", "--debug")),
    "timeout": ("sk", "vf", ("--signal", "--kill-after"), ("--foreground", "--preserve-status", "--verbose")),
    "stdbuf": ("ioe", "", ("--input", "--output", "--error"), ()),
    "xargs": ("INPLdsEa", "0rtpxoiel", ("--max-args", "--max-procs", "--delimiter", "--max-lines", "--arg-file"),
              ("--null", "--no-run-if-empty", "--verbose", "--interactive", "--exit")),
    "nice": ("n", "", ("--adjustment",), ()), "ionice": ("cnp", "t", (), ()), "chronic": ("", "ev", (), ()),
    "watch": ("ndw", "bcdeghpqtxv", (), ()),
}
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
HEREDOC = re.compile(r"(?<!<)<<-?[ \t]*(?:'([^']*)'|\"([^\"]*)\"|\\?([A-Za-z0-9_]+))")
COMMENT = re.compile(r"(^|(?<=\s))#.*$", re.M)
DOLLAR, TICK = "\ue000", "\ue001"  # `$` and a backtick inside single quotes: they run nothing
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
GIT_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
# ssh options that run a program, or hop through another one: never allowed automatically.
RISKY_SSH_OPTIONS = ("proxycommand", "localcommand", "permitlocalcommand", "knownhostscommand",
                     "proxyjump")
SSH_WITH_VALUE = set("bcDEeFIiJLlmOopQRSWw")
RISKY_SSH_LETTERS = set("FSWJ")  # config file (ProxyCommand), control socket, forwarding: never automatic
SSH_FLAGS = set("46AaCfGgKkMNnqsTtVvXxYy")


def lexer_for(command: str) -> shlex.shlex:
    """A shlex that keeps `#` inside a word (bash only starts a comment at a word start) and splits lines."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";()<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return lexer


def simple_argv(command: str) -> Optional[List[str]]:
    """The words of a plain command; None when it is compound or cannot be parsed."""
    if not command.strip() or any(mark in command for mark in ("\n", "\r", "`", "$(", "${")):
        return None
    try:
        words = list(lexer_for(command))
    except ValueError:  # e.g. an unclosed quote
        return None
    if not words or any(word and set(word) <= OPERATORS for word in words):
        return None
    return words


def segments(command: str) -> List[List[str]]:
    """Words of each part of a command (split at operators, newlines and parentheses); ValueError if unparsable."""
    parts: List[List[str]] = [[]]
    for word in lexer_for(command):
        if set(word) <= OPERATORS | {"\n"}:
            parts.append([])
        else:
            parts[-1].append(word)
    return [part for part in parts if part]


def option_end(wrapper: str, words: List[str]) -> Optional[int]:
    """How many words the option words[0] of a wrapper takes (with its value); None if it is not known."""
    short_value, short_flag, long_value, long_flag = WRAPPERS[wrapper]
    word = words[0]
    if word.startswith("--"):
        name = word.split("=")[0]
        if "=" in word and (name in long_value or name in long_flag):
            return 1
        return 2 if name in long_value and len(words) > 1 else (1 if name in long_flag else None)
    for j, letter in enumerate(word[1:], 1):
        if letter in short_value:
            return 1 if word[j + 1:] else min(2, len(words))
        if letter not in short_flag:
            return None
    return 1


def skip_prefix(words: List[str]) -> Tuple[List[str], bool]:
    """The command proper: after VAR=x, keywords and wrappers and their options (with their values).
    The flag is True when a wrapper option was not understood: the caller then scans every word."""
    while words:
        head = os.path.basename(words[0])
        if ASSIGNMENT.match(words[0]) or head in KEYWORDS:
            words = words[1:]
        elif head in WRAPPERS:
            words = words[1:]
            while words and (words[0].startswith("-") and words[0] != "-" or ASSIGNMENT.match(words[0])
                             or re.fullmatch(r"\d+(\.\d+)?[smhd]?|\{\}", words[0])):
                if words[0] == "--":
                    words = words[1:]
                    break
                step = option_end(head, words) if words[0].startswith("-") else 1
                if step is None:
                    return words, True
                words = words[step:]
        else:
            break
    return words, False


def denied_name(words: List[str], depth: int = 0) -> Optional[str]:
    """The DENIED_COMMANDS entry that these words (after wrappers and git options) start with."""
    words, unsure = skip_prefix(words)
    if unsure:
        firsts = {name.split()[0] for name in DENIED_COMMANDS}
        return next((os.path.basename(w) for w in words if os.path.basename(w) in firsts), None)
    if not words:
        return None
    head = os.path.basename(words[0])
    rest = words[1:]
    if head == "git":
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in GIT_WITH_VALUE else rest[1:]
    if head in SHELLS or head == "eval":
        script = rest if head == "eval" else None
        if head in SHELLS:
            flags = [i for i, w in enumerate(rest) if w.startswith("-") and not w.startswith("--") and "c" in w]
            script = rest[flags[0] + 1:flags[0] + 2] if flags else None
        if script:
            return command_denied(" ".join(script).replace(DOLLAR, "$").replace(TICK, "`"), depth + 1)
    if head == "find":
        for i, word in enumerate(rest):
            if word in FIND_EXEC:
                hit = denied_name(rest[i + 1:], depth + 1)
                if hit:
                    return hit
    found = None
    named = [head] + rest
    for name in DENIED_COMMANDS:
        parts = name.split()
        if named[:len(parts)] == parts:
            found = name
            break
    if found:
        return found
    for word in words:  # `$(...)` and backticks inside a quoted word still run in bash
        for inner in re.findall(r"\$\((.*)\)|`([^`]*)`", word):
            hit = command_denied(inner[0] or inner[1], depth + 1)
            if hit:
                return hit
    return None


def strip_heredocs(command: str) -> str:
    """The command without the bodies of here-documents (`<<EOF`, `<<'EOF'`, `<<-EOF`)."""
    kept, waiting = [], []
    for line in command.split("\n"):
        if waiting:
            if line.strip("\t" if waiting[0][1] else "") == waiting[0][0]:
                waiting.pop(0)
            continue
        kept.append(line)
        waiting = [(m.group(1) or m.group(2) or m.group(3), m.group(0).startswith("<<-"))
                   for m in HEREDOC.finditer(line)]
    return "\n".join(kept)


def mask_single_quotes(command: str) -> str:
    """Hide `$` and backticks inside single quotes (not run by bash); comments are left alone."""
    out, quote, i = [], "", 0
    while i < len(command):
        char = command[i]
        if not quote and char == "#" and (i == 0 or command[i - 1] in " \t\n"):
            end = command.find("\n", i)
            end = len(command) if end < 0 else end
            out.append(command[i:end])
            i = end
            continue
        if char == "\\" and quote != "'" and i + 1 < len(command):
            out.append(command[i:i + 2])
            i += 2
            continue
        if quote == "'":
            char = DOLLAR if char == "$" else TICK if char == "`" else char
        if char in "'\"" and quote in ("", char):
            quote = "" if quote else char
        out.append(char)
        i += 1
    return "".join(out)


def command_denied(command: str, depth: int = 0) -> Optional[str]:
    """The DENIED_COMMANDS entry a command runs, also behind wrappers, `bash -c` and `$(...)`.
    What cannot be parsed counts as denied."""
    if depth > 5:
        return "unparsable"
    command = mask_single_quotes(strip_heredocs(command))
    try:
        parts = segments(command)
    except ValueError:  # an unclosed quote: it may sit in a comment (`# it's`), so read it again without them
        command = COMMENT.sub("", command)
        try:
            parts = segments(command)
        except ValueError:
            return "unparsable"
    try:  # an unquoted backtick starts a command: read it as a separator too
        parts += segments(command.replace("`", " ; "))
    except ValueError:
        pass
    for words in parts:
        found = denied_name(words, depth)
        if found:
            return found
    return None


def ssh_target(argv: List[str]) -> Optional[Tuple[str, List[str]]]:
    """(host, remote command words) of an `ssh` call; None when it is risky or not understood."""
    if not argv or os.path.basename(argv[0]) != "ssh":
        return None
    i = 1
    while i < len(argv):
        word = argv[i]
        if word == "--":
            i += 1
            break
        if not word.startswith("-") or word == "-":
            break
        letters, j = word[1:], 0
        while j < len(letters):
            letter = letters[j]
            if letter in SSH_FLAGS:
                j += 1
                continue
            if letter not in SSH_WITH_VALUE:
                return None
            value = letters[j + 1:] or (argv[i + 1] if i + 1 < len(argv) else None)
            if value is None:
                return None
            if letter in RISKY_SSH_LETTERS or (letter == "o" and risky_option(value)):
                return None
            i += 0 if letters[j + 1:] else 1
            break
        i += 1
    if i >= len(argv):
        return None
    host = argv[i].split("@")[-1]
    if argv[i].startswith("ssh://"):
        host = re.sub(r"^ssh://(?:[^@/]*@)?([^:/]+).*$", r"\1", argv[i])
    return (host, argv[i + 1:]) if host and not host.startswith("-") else None


def risky_option(value: str) -> bool:
    key = re.split(r"[=\s]", value.strip(), maxsplit=1)[0].lower()
    return key in RISKY_SSH_OPTIONS


def unreadable_ssh(argv: List[str]) -> bool:
    """An ssh call that ssh_target rejects (risky option or not understood)."""
    return bool(argv) and os.path.basename(argv[0]) == "ssh" and ssh_target(argv) is None
