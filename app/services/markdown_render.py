"""Safe Markdown -> HTML for admin-written answers.

Admins write answers in Markdown (bold, lists, links, tables). The output is
shown to every chat user, so it must never carry script:

* raw HTML in the source is NOT passed through; it is escaped and shown as text
* links/images are only kept for http(s), mailto, tel and site-relative URLs
  (javascript:, data:, vbscript:, ... are stripped, including entity-encoded tricks)
* external links open in a new tab with rel="noopener noreferrer nofollow"

This replaces an HTML sanitiser library (nh3/bleach): Windows Smart App Control
blocks nh3's compiled module on this machine, and not emitting HTML at all is the
stronger guarantee anyway.
"""

import html
import re

import markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor
from markdown.treeprocessors import Treeprocessor

SAFE_SCHEMES = {"http", "https", "mailto", "tel"}
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
_INVISIBLE = re.compile(r"[\x00-\x20\x7f-\x9f​-‏ - ﻿]+")


def is_safe_url(url, allow_relative=True):
    """True for http(s)/mailto/tel URLs and (optionally) site-relative paths like /admin or #top."""
    if not isinstance(url, str):
        return False
    # Browsers ignore control/whitespace characters and decode entities before reading
    # the scheme ("jav&#x09;ascript:"), so normalise the same way before checking.
    value = _INVISIBLE.sub("", html.unescape(html.unescape(url)))
    if not value:
        return False
    match = _SCHEME.match(value)
    if match:
        return match.group(1).lower() in SAFE_SCHEMES
    if not allow_relative:
        return False
    return (value.startswith("/") and not value.startswith("//") and "\\" not in value) or value.startswith("#")


class _SafeLinksTreeprocessor(Treeprocessor):
    def run(self, root):
        for element in root.iter():
            # Markdown never produces event-handler or style attributes, but be explicit.
            for attribute in list(element.attrib):
                if attribute.lower().startswith("on") or attribute.lower() == "style":
                    del element.attrib[attribute]

            if element.tag == "a":
                href = element.get("href")
                if not is_safe_url(href):
                    element.attrib.pop("href", None)
                elif re.match(r"^\s*https?:", href, re.IGNORECASE):
                    element.set("target", "_blank")
                    element.set("rel", "noopener noreferrer nofollow")
            elif element.tag == "img":
                src = element.get("src")
                if not is_safe_url(src) or src.strip().lower().startswith(("mailto:", "tel:")):
                    element.attrib.pop("src", None)
                element.set("loading", "lazy")


_LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+\S")


class _ListAfterParagraphPreprocessor(Preprocessor):
    """Standard Markdown needs a blank line before a list. Admins usually don't
    know that, so a list written straight under a sentence still becomes a list."""

    def run(self, lines):
        result = []
        for line in lines:
            if (_LIST_ITEM.match(line) and result and result[-1].strip()
                    and not _LIST_ITEM.match(result[-1]) and not result[-1].startswith((" ", "\t"))):
                result.append("")
            result.append(line)
        return result


class _SafeMarkdownExtension(Extension):
    def extendMarkdown(self, md):
        # Remove the raw-HTML pass-through so "<script>" etc. are escaped as text.
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")
        md.preprocessors.register(_ListAfterParagraphPreprocessor(md), "list_after_paragraph", 25)
        md.treeprocessors.register(_SafeLinksTreeprocessor(md), "safe_links", 0)


def render_markdown(text):
    """Render admin Markdown to safe HTML (string)."""
    if not text:
        return ""
    renderer = markdown.Markdown(
        extensions=["sane_lists", "nl2br", "tables", _SafeMarkdownExtension()],
        output_format="html",
    )
    return renderer.convert(text)


def compose_answer_markdown(answer, assistant_name="Assistant"):
    """Apply the answer's template (if any) and fill placeholders."""
    body = answer.body_markdown or ""
    template = getattr(answer, "template", None)
    if template is not None:
        body = template.body_markdown.replace("{answer}", body)
    return body.replace("{assistant_name}", assistant_name)


def safe_links(links):
    """Only links with safe URLs, as [{"label", "url", "external"}]."""
    result = []
    for link in links or []:
        url = (link or {}).get("url")
        if is_safe_url(url):
            result.append({
                "label": (link.get("label") or url)[:120],
                "url": url.strip(),
                "external": bool(re.match(r"^https?:", url.strip(), re.IGNORECASE)),
            })
    return result
