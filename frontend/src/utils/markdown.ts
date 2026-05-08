import katex from 'katex';
import Prism from 'prismjs';
import 'prismjs/themes/prism-tomorrow.css';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-markdown';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-rust';

// Use non-printable control chars as stash delimiters - safe from markdown processing
const STASH_PREFIX = '\x02';
const STASH_SUFFIX = '\x03';

interface StashState {
  map: Map<string, string>;
  counter: number;
}

function createStash(): StashState {
  return { map: new Map(), counter: 0 };
}

function stashPut(state: StashState, content: string): string {
  const key = `${STASH_PREFIX}${state.counter++}${STASH_SUFFIX}`;
  state.map.set(key, content);
  return key;
}

// Single pass restore - no loops, no recursion
function restore(state: StashState, html: string): string {
  if (state.map.size === 0) return html;
  let result = html;
  state.map.forEach((value, key) => {
    result = result.split(key).join(value);
  });
  return result;
}

function escapeHTML(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function decodeHTMLEntities(text: string): string {
  const map: Record<string, string> = {
    '&lt;': '<', '&gt;': '>', '&amp;': '&',
    '&quot;': '"', '&#39;': "'", '&nbsp;': ' ',
    '&#60;': '<', '&#62;': '>', '&#x3C;': '<', '&#x3E;': '>',
  };
  let result = text;
  for (const [entity, char] of Object.entries(map)) {
    result = result.split(entity).join(char);
  }
  return result;
}

function protectCodeBlocks(state: StashState, text: string): string {
  return text.replace(/(`{3,}|~{3,})(\w*)\n([\s\S]*?)\1/g, (_, _ticks, lang, code) => {
    const langAttr = (lang || 'text').trim();
    // Remove trailing newline before closing fence
    const trimmedCode = code.endsWith('\n') ? code.slice(0, -1) : code;
    let highlighted: string;
    try {
      const grammar = Prism.languages[langAttr];
      highlighted = grammar
        ? Prism.highlight(trimmedCode, grammar, langAttr)
        : escapeHTML(trimmedCode);
    } catch {
      highlighted = escapeHTML(trimmedCode);
    }
    const html = `<pre class="language-${langAttr}"><code class="language-${langAttr}">${highlighted}</code></pre>`;
    return stashPut(state, html);
  });
}

function protectInlineCode(state: StashState, text: string): string {
  return text.replace(/`([^`\n]+)`/g, (_, code) => {
    return stashPut(state, `<code class="inline-code">${escapeHTML(code)}</code>`);
  });
}

function processMathBlocks(state: StashState, text: string): string {
  // Display math
  let result = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, math) => {
    try {
      return stashPut(state, `<div class="katex-block">${katex.renderToString(math, { displayMode: true })}</div>`);
    } catch {
      return stashPut(state, `<div class="katex-block katex-error">${escapeHTML(math)}</div>`);
    }
  });
  // Inline math
  result = result.replace(/\$([^\s$][^$]*?[^\s$])\$/g, (_, math) => {
    try {
      return stashPut(state, `<span class="katex-inline">${katex.renderToString(math, { displayMode: false })}</span>`);
    } catch {
      return stashPut(state, `<span class="katex-inline katex-error">${escapeHTML(math)}</span>`);
    }
  });
  return result;
}

// Inline formatting - applied to text segments only
function applyInlineFormatting(text: string): string {
  let r = text;
  // Images before links
  r = r.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" loading="lazy" />');
  // Links
  r = r.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  // Bold+italic
  r = r.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  // Bold
  r = r.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  r = r.replace(/__(.+?)__/g, '<strong>$1</strong>');
  // Italic
  r = r.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  r = r.replace(/_([^_\n]+)_/g, '<em>$1</em>');
  // Strikethrough
  r = r.replace(/~~(.+?)~~/g, '<del>$1</del>');
  return r;
}

function processMarkdownLines(text: string): string {
  const lines = text.split('\n');
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Stash token (code block placeholder) - output as-is
    if (line.startsWith(STASH_PREFIX)) {
      out.push(line);
      i++;
      continue;
    }

    // Headings
    let m = line.match(/^(#{1,6})\s+(.+)$/);
    if (m) {
      const level = m[1].length;
      out.push(`<h${level}>${applyInlineFormatting(m[2])}</h${level}>`);
      i++;
      continue;
    }

    // Horizontal rule
    if (/^(\*{3,}|-{3,}|_{3,})$/.test(line.trim())) {
      out.push('<hr />');
      i++;
      continue;
    }

    // Blockquote
    if (line.startsWith('> ')) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].startsWith('> ')) {
        quoteLines.push(lines[i].slice(2));
        i++;
      }
      out.push(`<blockquote>${processMarkdownLines(quoteLines.join('\n'))}</blockquote>`);
      continue;
    }

    // Unordered list
    if (/^[-*+] /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*+] /.test(lines[i])) {
        items.push(`<li>${applyInlineFormatting(lines[i].replace(/^[-*+] /, ''))}</li>`);
        i++;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${applyInlineFormatting(lines[i].replace(/^\d+\.\s+/, ''))}</li>`);
        i++;
      }
      out.push(`<ol>${items.join('')}</ol>`);
      continue;
    }

    // Empty line
    if (line.trim() === '') {
      out.push('');
      i++;
      continue;
    }

    // Paragraph: collect consecutive "plain" lines
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !lines[i].startsWith(STASH_PREFIX) &&
      !/^#{1,6}\s/.test(lines[i]) &&
      !/^[-*+] /.test(lines[i]) &&
      !/^\d+\.\s/.test(lines[i]) &&
      !lines[i].startsWith('> ') &&
      !/^(\*{3,}|-{3,}|_{3,})$/.test(lines[i].trim())
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length > 0) {
      const inner = paraLines.map(l => applyInlineFormatting(l)).join('<br />');
      out.push(`<p>${inner}</p>`);
    }
  }

  return out.join('\n');
}

function sanitizeHTML(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/\son\w+="[^"]*"/gi, '')
    .replace(/javascript:/gi, '');
}

export function renderMarkdown(raw: string): string {
  if (!raw) return '';

  const state = createStash();

  // 1. Decode pre-escaped HTML entities
  let html = decodeHTMLEntities(raw);

  // 2. Protect fenced code blocks (syntax highlighted)
  html = protectCodeBlocks(state, html);

  // 3. Protect inline code
  html = protectInlineCode(state, html);

  // 4. Protect math
  html = processMathBlocks(state, html);

  // 5. Process markdown structure
  html = processMarkdownLines(html);

  // 6. Restore stashed content (single pass, safe)
  html = restore(state, html);

  // 7. Sanitize
  html = sanitizeHTML(html);

  return html;
}

export function highlightCode(code: string, lang: string): string {
  try {
    const grammar = Prism.languages[lang] || Prism.languages.text;
    return Prism.highlight(code, grammar, lang);
  } catch {
    return code;
  }
}

export { Prism };
