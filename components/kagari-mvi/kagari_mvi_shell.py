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

# Denied unless the ledger allows them: a speed bump, not a boundary (python3 -c can still reach the network).
DENIED_COMMANDS = ("curl", "wget", "gh", "git push", "git remote", "ssh", "scp", "sudo", "su",
                   "nc", "ncat", "socat", "telnet")
OPERATORS = set("();<>|&")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
KEYWORDS = {"time", "if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "exec", "command",
            "builtin"}
WRAPPERS = {"env", "nohup", "xargs", "nice", "timeout", "stdbuf", "setsid", "ionice", "chronic", "watch"}
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


def skip_prefix(words: List[str]) -> List[str]:
    """The command proper: after VAR=x, keywords and wrappers (env, time, xargs, ...) and their options."""
    while words:
        head = os.path.basename(words[0])
        if ASSIGNMENT.match(words[0]) or head in KEYWORDS:
            words = words[1:]
        elif head in WRAPPERS:
            words = words[1:]
            while words and (words[0].startswith("-") or ASSIGNMENT.match(words[0])
                             or re.fullmatch(r"\d+[smhd]?|\{\}", words[0])):
                words = words[1:]
        else:
            break
    return words


def denied_name(words: List[str], depth: int = 0) -> Optional[str]:
    """The DENIED_COMMANDS entry that these words (after wrappers and git options) start with."""
    words = skip_prefix(words)
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
            return command_denied(" ".join(script), depth + 1)
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


def command_denied(command: str, depth: int = 0) -> Optional[str]:
    """The DENIED_COMMANDS entry a command runs, also behind wrappers, `bash -c` and `$(...)`.
    What cannot be parsed counts as denied."""
    if depth > 5:
        return "unparsable"
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
