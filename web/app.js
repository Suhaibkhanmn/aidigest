const els = {
  currentDate: document.querySelector("#currentDate"),
  issueDate: document.querySelector("#issueDate"),
  digestTitle: document.querySelector("#digestTitle"),
  digestContent: document.querySelector("#digestContent"),
  archiveList: document.querySelector("#archiveList"),
  citationList: document.querySelector("#citationList"),
  telegramLinks: [
    document.querySelector("#telegramJoinTop"),
    document.querySelector("#telegramJoinHero"),
    document.querySelector("#telegramJoinRail")
  ].filter(Boolean)
};

const params = new URLSearchParams(window.location.search);
const isDigestView = params.has("issue") || params.has("latest");
document.body.classList.add(isDigestView ? "digest-view" : "home-view");

function markdownToHtml(markdown) {
  const cleanedMarkdown = stripDocumentChrome(markdown);
  const linksByText = extractLinks(markdown);
  const escaped = cleanedMarkdown
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");

  let inList = false;
  let currentSection = "";
  let inStory = false;
  const lines = escaped.split("\n");
  const html = [];
  const closeList = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };
  const closeStory = () => {
    if (inStory) {
      html.push("</section>");
      inStory = false;
    }
  };
  for (const line of lines) {
    if (line.startsWith("## ")) {
      closeList();
      closeStory();
      currentSection = line.slice(3).trim();
      html.push(`<h2>${inline(line.slice(3))}</h2>`);
      continue;
    }
    if (line.startsWith("### ")) {
      closeList();
      if (currentSection === "What Happened") {
        closeStory();
        html.push('<section class="story-segment">');
        inStory = true;
      }
      html.push(`<h3>${inline(line.slice(4))}</h3>`);
      continue;
    }
    if (line.startsWith("- ")) {
      if (!inList) {
        html.push(currentSection === "Smaller Notes" ? '<ul class="note-list">' : "<ul>");
        inList = true;
      }
      const listText = currentSection === "Smaller Notes"
        ? smallerNoteInline(line.slice(2), linksByText)
        : inline(line.slice(2));
      html.push(`<li>${listText}</li>`);
      continue;
    }
    closeList();
    if (line.startsWith("# ")) {
      closeStory();
      html.push(`<h1>${inline(line.slice(2))}</h1>`);
    } else if (!line.trim()) {
      html.push("");
    } else if (/^\[Read more\]\(https?:\/\/[^)]+\)$/.test(line.trim())) {
      html.push(`<p class="read-more-line">${inline(line)}</p>`);
    } else {
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  closeStory();
  return html.join("");
}

function smallerNoteInline(text, links) {
  let cleaned = stripTitleQuotes(text);
  if (/\[Read more\]\(https?:\/\/[^)]+\)/.test(cleaned)) {
    return inline(cleaned);
  }
  const url = sourceUrlForNote(cleaned, links);
  const html = inline(cleaned);
  if (!url) return html;
  return `${html} <a class="note-source" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">Read more</a>`;
}

function stripTitleQuotes(text) {
  return text.replace(/"([^"]{8,160})"/g, "$1");
}

function sourceUrlForNote(note, links) {
  const noteTokens = tokenSet(note);
  let best = { url: "", score: 0 };
  links.forEach((link) => {
    const score = overlap(noteTokens, tokenSet(`${link.label} ${hostname(link.url)}`));
    if (score > best.score) best = { url: link.url, score };
  });
  return best.score >= 0.12 ? best.url : "";
}

function tokenSet(text) {
  const stop = new Set(["the", "and", "for", "with", "that", "this", "from", "into", "also", "paper", "article", "published", "reported"]);
  return new Set(
    String(text)
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, " ")
      .split(/\s+/)
      .filter((token) => token.length > 3 && !stop.has(token))
  );
}

function overlap(left, right) {
  if (!left.size || !right.size) return 0;
  let shared = 0;
  left.forEach((token) => {
    if (right.has(token)) shared += 1;
  });
  return shared / Math.max(left.size, right.size);
}

function stripDocumentChrome(markdown) {
  const hidden = removeSections(markdown, ["For Your Work", "Closing"]);
  const lines = hidden.split("\n");
  const stripped = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("# AI Digest")) continue;
    if (/^##\s+[A-Z][a-z]+ \d{1,2}, \d{4}$/.test(trimmed)) continue;
    stripped.push(line);
  }
  return stripped.join("\n").trim();
}

function removeSections(markdown, sectionTitles) {
  const escapedTitles = sectionTitles.map((title) => title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const pattern = new RegExp(`\\n##\\s+(${escapedTitles})\\s*\\n[\\s\\S]*?(?=\\n##\\s+|$)`, "g");
  return markdown.replace(pattern, "");
}

function inline(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

async function loadPublicConfig() {
  try {
    const response = await fetch("/api/public-config");
    const data = await response.json();
    const url = data.telegram_bot_url || "";
    els.telegramLinks.forEach((link) => {
      if (!url) {
        if (link.id === "telegramJoinTop") {
          link.hidden = true;
        }
        link.removeAttribute("href");
        link.setAttribute("aria-disabled", "true");
        link.classList.add("unavailable");
        link.textContent = "Telegram setup pending";
        return;
      }
      link.hidden = false;
      link.href = url;
      link.classList.remove("unavailable");
      link.removeAttribute("aria-disabled");
    });
  } catch {
    els.telegramLinks.forEach((link) => {
      if (link.id === "telegramJoinTop") {
        link.hidden = true;
      }
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
      link.classList.add("unavailable");
      link.textContent = "Telegram setup pending";
    });
  }
}

async function loadArchive() {
  const response = await fetch("/api/digests");
  const data = await response.json();
  const digests = data.digests.slice(0, 12);
  if (!digests.length) {
    els.archiveList.innerHTML = "<p class='meta-line'>No issues yet.</p>";
    els.digestTitle.textContent = "No issue yet";
    els.digestContent.innerHTML = "<p>The first issue will appear here after the next scheduled run.</p>";
    renderCitations("");
    return;
  }
  els.archiveList.innerHTML = "";
  const requestedIssue = new URLSearchParams(window.location.search).get("issue");
  let shownIssue = false;
  digests.forEach((digest, index) => {
    const button = document.createElement("button");
    button.className = "archive-item";
    const label = volumeIssueLabel(digest, digests);
    button.innerHTML = `
      <strong>${label}</strong>
      <small>${issueMeta(digest)}</small>
    `;
    button.addEventListener("click", () => showDigest(digest.content, label));
    els.archiveList.appendChild(button);
    const digestSlug = digest.name.replace(/\.md$/, "");
    const shouldShowRequested = requestedIssue && digestSlug === requestedIssue;
    if (shouldShowRequested || (!shownIssue && index === 0 && els.digestTitle.textContent === "Loading latest issue")) {
      showDigest(digest.content, label);
      shownIssue = true;
    }
  });
}

function showDigest(markdown, title) {
  els.digestTitle.textContent = title;
  els.issueDate.textContent = dateFromMarkdown(markdown) || "Current Issue";
  els.digestContent.innerHTML = markdownToHtml(markdown);
  renderCitations(markdown);
}

function renderCitations(markdown) {
  const links = extractLinks(markdown);
  if (!links.length) {
    els.citationList.innerHTML = "<p class='meta-line'>No read-more links in this issue yet.</p>";
    return;
  }
  els.citationList.innerHTML = "";
  links.slice(0, 12).forEach((link, index) => {
    const anchor = document.createElement("a");
    anchor.className = "citation-item";
    anchor.href = link.url;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    anchor.innerHTML = `
      <span>${index + 1}</span>
      <strong>${escapeHtml(link.label)}</strong>
      <small>${escapeHtml(hostname(link.url))}</small>
    `;
    els.citationList.appendChild(anchor);
  });
}

function extractLinks(markdown) {
  const seen = new Set();
  const links = [];
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g;
  let match;
  while ((match = pattern.exec(markdown)) !== null) {
    const label = match[1].trim();
    const url = match[2].trim();
    if (!url || seen.has(url)) continue;
    seen.add(url);
    links.push({ label, url });
  }
  return links;
}

function dateFromMarkdown(markdown) {
  const match = markdown.match(/^##\s+([A-Z][a-z]+ \d{1,2}, \d{4})/m);
  return match ? match[1] : "";
}

function issueMeta(digest) {
  const date = dateFromName(digest.name);
  return date || (digest.telegram_content ? "Telegram saved" : "Website issue");
}

function dateFromName(name) {
  const match = String(name).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return "";
  return `${match[3]}.${match[2]}.${match[1]}`;
}

function volumeIssueLabel(digest, digests) {
  const ordered = [...digests].sort((a, b) => {
    const aTime = Number(a.modified || 0);
    const bTime = Number(b.modified || 0);
    if (aTime !== bTime) return aTime - bTime;
    return String(a.name).localeCompare(String(b.name));
  });
  const index = ordered.findIndex((entry) => entry.name === digest.name);
  return `Vol.1 #${index >= 0 ? index + 1 : 1}`;
}

function hostname(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

els.currentDate.textContent = new Date().toLocaleDateString(undefined, {
  weekday: "long",
  month: "long",
  day: "numeric",
  year: "numeric"
});

loadPublicConfig();
loadArchive();
