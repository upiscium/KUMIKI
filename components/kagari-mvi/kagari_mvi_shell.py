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
SEGMENT = re.compile(r"&&|\|\||[;|&\n]")
# ssh options that run a program, or hop through another one: never allowed automatically.
RISKY_SSH_OPTIONS = ("proxycommand", "localcommand", "permitlocalcommand", "knownhostscommand",
                     "proxyjump")
SSH_WITH_VALUE = set("bcDEeFIiJLlmOopQRSWw")
SSH_FLAGS = set("46AaCfGgKkMNnqsTtVvXxYy")


def simple_argv(command: str) -> Optional[List[str]]:
    """The words of a plain command; None when it is compound or cannot be parsed."""
    if not command.strip() or any(mark in command for mark in ("\n", "\r", "`", "$(", "${")):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:  # e.g. an unclosed quote
        return None
    if not words or any(word and set(word) <= OPERATORS for word in words):
        return None
    return words


def segment_words(command: str) -> List[List[str]]:
    """Rough words of each part of a command that simple_argv cannot read (for the denied check)."""
    return [part.split() for part in SEGMENT.split(command) if part.strip()]


def denied_name(words: List[str]) -> Optional[str]:
    """The DENIED_COMMANDS entry that these words start with."""
    if not words:
        return None
    head = [os.path.basename(words[0])] + words[1:]
    for name in DENIED_COMMANDS:
        parts = name.split()
        if head[:len(parts)] == parts:
            return name
    return None


def command_denied(command: str) -> Optional[str]:
    argv = simple_argv(command)
    for words in ([argv] if argv is not None else segment_words(command)):
        found = denied_name(words)
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
            if letter == "J" or (letter == "o" and risky_option(value)):
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
