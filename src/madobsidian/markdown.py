"""Lex supported single-line attachment links in active Markdown text."""

from dataclasses import dataclass
import posixpath
import re
from urllib.parse import quote, unquote


@dataclass(frozen=True)
class Reference:
    start: int
    end: int
    raw: str
    target: str
    before: str
    after: str
    markdown: bool

    def render(self, target: str, note: str) -> str:
        if self.markdown:
            target = quote(posixpath.relpath(target, posixpath.dirname(note) or "."), safe="/~")
        return self.before + target + self.after


_TOKEN = re.compile(r"<!--|\x60+|!?\[|\\.")
_FENCE = re.compile(r" {0,3}(\x60{3,}|~{3,})(.*)")
_WIKI = re.compile(r"!?\[\[(?P<target>[^\n|#]*?)(?:[#|][^\n]*?)?\]\]")
_LINK = re.compile(r'!?\[[^\[\]\n]*\]\((?:<(?P<angle>[^>\n]+)>|(?P<plain>[^()\s]+))(?: +"[^"\n]*")?\)')


def references(text: str):
    offset = 0
    fence = ""
    frontmatter = False
    comment = False
    code_end = 0
    for line in text.splitlines(keepends=True):
        base = offset
        offset += len(line)
        if base == 0 and line.lstrip("\ufeff").rstrip() == "---":
            frontmatter = True
            continue
        if frontmatter:
            frontmatter = line.rstrip() not in {"---", "..."}
            continue
        content = re.sub(r"^(?: {0,3}> ?)+", "", line)
        marker = _FENCE.match(re.sub(r"^ {0,3}(?:[-+*]|\d+[.)])[ \t]+", "", content))
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
            continue
        if marker and not comment:
            fence = marker[1]
            continue
        if not comment and content.startswith(("    ", "\t")):
            continue
        pos = max(0, code_end - base)
        while pos < len(line):
            if comment:
                close = line.find("-->", pos)
                if close < 0:
                    break
                pos = close + 3
                comment = False
                continue
            token = _TOKEN.search(line, pos)
            if not token:
                break
            pos = token.end()
            if token[0] == "<!--":
                comment = True
            elif token[0].startswith(chr(96)):
                end = re.search(r"(?m)^ {0,3}(?:\x60{3,}|~{3,})|\n[ \t]*\r?\n", text[base + pos:])
                limit = base + pos + end.start() if end else len(text)
                closer = re.compile(r"(?<!\x60)" + token[0] + r"(?!\x60)").search(text, base + pos, limit)
                if closer:
                    code_end = closer.end()
                    pos = code_end - base
            elif token[0].endswith("[") and not token[0].startswith("\\"):
                match = _WIKI.match(line, token.start())
                markdown = match is None
                if markdown:
                    match = _LINK.match(line, token.start())
                if match:
                    field = ("angle" if match["angle"] else "plain") if markdown else "target"
                    raw = match[field]
                    path = raw.partition("#")[0] if markdown else raw
                    yield Reference(base + match.start(), base + match.end(), match[0],
                                    unquote(path) if markdown else path,
                                    line[match.start():match.start(field)],
                                    line[match.start(field) + len(path):match.end()], markdown)
                    pos = match.end()
