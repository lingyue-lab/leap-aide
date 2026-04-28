import katex from 'katex';
import Prism from 'prismjs';
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

// Stash/restore patterns for protecting content during processing
const STASH_PATTERNS = {
  fence: /(`{3,})(\w*)\n([\s\S]*?)\1/g,
  inlineCode: /`([^`\n]+)`/g,
  mathBlock: /\$\$([\s\S]+?)\$\$/g,
  mathInline: /\$([^\s$][^$]*?[^\s$])\$/g,
  media: /~~M:(\w+):(\d+)~~/g,
  link: /\[([^\]]*)\]\(([^)]+)\)/g,
};

const STASH_KEY = '___STASH___';
const stashMap: Map<string, string> = new Map();
let stashCounter = 0;

function stash(content: string): string {
  const key = `${STASH_KEY}${stashCounter++}___`;
  stashMap.set(key, content);
  return key;
}

function restore(html: string): string {
  let result = html;
  stashMap.forEach((value, key) => {
    result = result.replace(key, value);
  });
  return result;
}

function decodeHTMLEntities(text: string): string {
  const entities: Record<string, string> = {
    '&#60;': '<',
    '&#62;': '>',
    '&#x3C;': '<',
    '&#x3E;': '>',
    '&lt;': '<',
    '&gt;': '>',
    '&amp;': '&',
    '&quot;': '"',
    '&#39;': "'",
    '&nbsp;': ' ',
  };
  
  let result = text;
  Object.entries(entities).forEach(([entity, char]) => {
    result = result.split(entity).join(char);
  });
  return result;
}

function protectCodeBlocks(text: string): string {
  // Protect fenced code blocks
  return text.replace(STASH_PATTERNS.fence, (_, _ticks, lang, code) => {
    const escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return stash(`<pre><code class="language-${lang || 'text'}">${escaped}</code></pre>`);
  });
}

function protectInlineCode(text: string): string {
  return text.replace(STASH_PATTERNS.inlineCode, (_, code) => {
    const escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return stash(`<code>${escaped}</code>`);
  });
}

function processMathBlocks(text: string): string {
  // Display math $$...$$
  let result = text.replace(STASH_PATTERNS.mathBlock, (_, math) => {
    try {
      return stash(`<div class="katex-block">${katex.renderToString(math, { displayMode: true })}</div>`);
    } catch {
      return stash(`<div class="katex-block katex-error">$${math}$</div>`);
    }
  });
  
  // Inline math $...$
  result = result.replace(STASH_PATTERNS.mathInline, (_, math) => {
    try {
      return stash(`<span class="katex-inline">${katex.renderToString(math, { displayMode: false })}</span>`);
    } catch {
      return stash(`<span class="katex-inline katex-error">$${math}$</span>`);
    }
  });
  
  return result;
}

function processCodeBlocks(html: string): string {
  // Apply syntax highlighting to code blocks
  return html.replace(/<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/g, (_, lang, code) => {
    const decodedCode = decodeURIComponent(code);
    try {
      const grammar = Prism.languages[lang] || Prism.languages.text;
      const highlighted = Prism.highlight(decodedCode, grammar, lang);
      return `<pre class="language-${lang}"><code class="language-${lang}">${highlighted}</code></pre>`;
    } catch {
      return `<pre class="language-${lang}"><code class="language-${lang}">${code}</code></pre>`;
    }
  });
}

function processInlineMarkdown(text: string): string {
  let result = text;
  
  // Headers
  result = result.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  result = result.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  result = result.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  
  // Bold and italic
  result = result.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  result = result.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  result = result.replace(/\*(.+?)\*/g, '<em>$1</em>');
  result = result.replace(/___(.+?)___/g, '<strong><em>$1</em></strong>');
  result = result.replace(/__(.+?)__/g, '<strong>$1</strong>');
  result = result.replace(/_(.+?)_/g, '<em>$1</em>');
  
  // Strikethrough
  result = result.replace(/~~(.+?)~~/g, '<del>$1</del>');
  
  // Blockquotes
  result = result.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
  
  // Horizontal rule
  result = result.replace(/^---$/gm, '<hr />');
  
  // Unordered lists
  result = result.replace(/^- (.+)$/gm, '<li>$1</li>');
  result = result.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
  
  // Ordered lists
  result = result.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
  
  // Links
  result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  
  // Images
  result = result.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" loading="lazy" />');
  
  // Line breaks and paragraphs
  result = result.split('\n\n').map(para => {
    if (para.trim() && !para.startsWith('<')) {
      return `<p>${para.replace(/\n/g, '<br />')}</p>`;
    }
    return para;
  }).join('\n');
  
  return result;
}

function sanitizeHTML(html: string): string {
  // Remove any script tags or dangerous content
  let result = html.replace(/<script[\s\S]*?<\/script>/gi, '');
  result = result.replace(/on\w+="[^"]*"/gi, '');
  result = result.replace(/javascript:/gi, '');
  return result;
}

export function renderMarkdown(raw: string): string {
  if (!raw) return '';
  
  // Clear stash
  stashMap.clear();
  stashCounter = 0;
  
  let html = raw;
  
  // 1. Decode HTML entities
  html = decodeHTMLEntities(html);
  
  // 2. Protect code blocks
  html = protectCodeBlocks(html);
  
  // 3. Protect inline code
  html = protectInlineCode(html);
  
  // 4. Process math blocks
  html = processMathBlocks(html);
  
  // 5. Process inline markdown
  html = processInlineMarkdown(html);
  
  // 6. Apply syntax highlighting
  html = processCodeBlocks(html);
  
  // 7. Restore protected content
  html = restore(html);
  
  // 8. Sanitize
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
