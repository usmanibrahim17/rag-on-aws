/*
 * RAG chat widget: one <script> tag, no framework, no build step.
 *
 *   <script src="widget.js" data-api-url="https://<api-id>.execute-api.us-east-1.amazonaws.com/ask"
 *           data-collection="fastapi"
 *           data-source-url-template="https://fastapi.tiangolo.com/tutorial/{slug}/"
 *           data-title="Ask the docs"></script>
 *
 * Attributes:
 *   data-api-url             (required) full URL of the POST /ask endpoint
 *   data-collection          (optional) restrict answers to one document collection
 *   data-source-url-template (optional) makes sources clickable. {path} is the source key
 *                            without "docs/" ("business/faq.md"); {slug} is {path} without its
 *                            extension ("business/faq"; "index" becomes empty).
 *   data-title               (optional) panel title
 *
 * Page API:
 *   window.RagWidget.ask("question")  opens the panel and asks it
 *   window.RagWidget.configure({collection, title, sourceUrlTemplate})  switches the knowledge
 *                                     base and starts a fresh conversation
 *
 * Adapted from the ai-support-widget project's widget.js (MIT): Shadow DOM isolation and the
 * el() helper are kept; the business/escalation logic was replaced with this API's response
 * (answer, numbered sources, latency, tokens). All text is inserted with textContent, never
 * innerHTML, so answers and sources cannot inject markup into the host page.
 */
(function () {
  "use strict";

  var SCRIPT =
    document.currentScript || document.querySelector('script[src*="widget.js"]');
  var API_URL = SCRIPT && SCRIPT.getAttribute("data-api-url");
  var COLLECTION = (SCRIPT && SCRIPT.getAttribute("data-collection")) || "";
  var SOURCE_TEMPLATE = (SCRIPT && SCRIPT.getAttribute("data-source-url-template")) || "";
  var TITLE = (SCRIPT && SCRIPT.getAttribute("data-title")) || "Ask the docs";
  var MAX_QUESTION_CHARS = 500; // the API rejects longer questions

  if (!API_URL) {
    console.error("[rag-widget] missing data-api-url on the <script> tag");
    return;
  }

  function el(tag, props, children) {
    var node = document.createElement(tag);
    Object.keys(props || {}).forEach(function (key) {
      if (key === "text") node.textContent = props[key];
      else node.setAttribute(key, props[key]);
    });
    (children || []).forEach(function (child) {
      node.appendChild(child);
    });
    return node;
  }

  // Fill the URL template for a source key (null when no template is configured).
  function sourceUrl(key) {
    if (!SOURCE_TEMPLATE) return null;
    var path = key.replace(/^docs\//, "");
    var slug = path.replace(/\.[A-Za-z0-9]+$/, "");
    if (slug === "index") slug = "";
    var url = SOURCE_TEMPLATE.split("{path}").join(path).split("{slug}").join(slug);
    return url.replace(/([^:])\/\/+/g, "$1/"); // an empty slug must not leave "//"
  }

  // The model (or the distance threshold) refused to answer from the documents.
  function isRefusal(data) {
    var text = (data.answer || "").toLowerCase();
    return text.indexOf("don't know") !== -1 || text.indexOf("no information") !== -1;
  }

  function init() {
    var host = document.createElement("div");
    host.id = "rag-chat-widget-host";
    document.body.appendChild(host);

    // Shadow DOM keeps the host page's CSS out of the widget and the widget's CSS out of the page.
    var shadow = host.attachShadow({ mode: "open" });
    var style = document.createElement("style");
    style.textContent = [
      ":host { all: initial; }",
      "* { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; }",
      ".bubble { position: fixed; bottom: 20px; right: 20px; width: 56px; height: 56px; border-radius: 50%;",
      "  background: #0f766e; color: #fff; border: none; font-size: 26px; cursor: pointer;",
      "  box-shadow: 0 4px 12px rgba(0,0,0,.25); z-index: 2147483000; }",
      ".bubble:hover { background: #115e59; }",
      ".bubble:focus-visible, .send:focus-visible, .input:focus-visible, a:focus-visible { outline: 2px solid #f59e0b; outline-offset: 2px; }",
      ".panel { position: fixed; bottom: 88px; right: 20px; width: 380px; max-width: calc(100vw - 40px);",
      "  height: 560px; max-height: calc(100vh - 120px); background: #fff; border-radius: 12px;",
      "  box-shadow: 0 8px 30px rgba(0,0,0,.25); display: flex; flex-direction: column; overflow: hidden; z-index: 2147483000; }",
      ".header { background: #0f766e; color: #fff; padding: 12px 16px; font-size: 15px; font-weight: 600; }",
      ".messages { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; background: #f9fafb; }",
      ".msg { max-width: 92%; padding: 8px 12px; border-radius: 12px; font-size: 13px; line-height: 1.45;",
      "  white-space: pre-wrap; word-wrap: break-word; }",
      ".user { align-self: flex-end; background: #0f766e; color: #fff; border-bottom-right-radius: 2px; }",
      ".bot { align-self: flex-start; background: #e5e7eb; color: #111827; border-bottom-left-radius: 2px; }",
      ".error { align-self: flex-start; background: #fee2e2; color: #991b1b; }",
      ".card { align-self: flex-start; max-width: 92%; display: flex; flex-direction: column; gap: 6px; }",
      ".card .msg { max-width: 100%; align-self: stretch; }",
      ".sources { margin: 0; padding: 0 0 0 4px; list-style: none; font-size: 12px; color: #374151; display: flex; flex-direction: column; gap: 3px; }",
      ".sources a { color: #0f766e; text-decoration: underline; }",
      ".sources .n { display: inline-block; min-width: 1.6em; font-weight: 600; color: #111827; }",
      ".meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 11px; color: #6b7280; }",
      ".pill { padding: 1px 8px; border-radius: 999px; background: #fef3c7; color: #92400e; font-weight: 600; }",
      ".typing { align-self: flex-start; font-size: 12px; color: #6b7280; padding: 4px 12px; }",
      ".row { display: flex; gap: 6px; padding: 10px; border-top: 1px solid #e5e7eb; background: #fff; }",
      ".input { flex: 1; padding: 8px 10px; border: 1px solid #9ca3af; border-radius: 8px; font-size: 13px; }",
      ".send { background: #0f766e; color: #fff; border: none; border-radius: 8px; padding: 0 14px; font-size: 13px; cursor: pointer; }",
      ".send:disabled { opacity: .5; cursor: not-allowed; }",
      ".hidden { display: none !important; }",
    ].join("\n");
    shadow.appendChild(style);

    var bubble = el("button", { class: "bubble", type: "button", "aria-label": "Open chat", text: "💬" });
    var messages = el("div", { class: "messages", "aria-live": "polite" });
    var input = el("input", {
      class: "input",
      type: "text",
      placeholder: "Ask a question...",
      maxlength: String(MAX_QUESTION_CHARS),
      "aria-label": "Your question",
    });
    var send = el("button", { class: "send", type: "button", text: "Send" });
    var panel = el("div", { class: "panel hidden", role: "dialog", "aria-label": TITLE }, [
      el("div", { class: "header", text: TITLE }),
      messages,
      el("div", { class: "row" }, [input, send]),
    ]);
    shadow.appendChild(panel);
    shadow.appendChild(bubble);

    var open = false;
    function setOpen(value) {
      open = value;
      panel.classList.toggle("hidden", !value);
      bubble.setAttribute("aria-label", value ? "Close chat" : "Open chat");
      if (value) input.focus();
    }
    bubble.addEventListener("click", function () {
      setOpen(!open);
    });

    function scrollDown() {
      messages.scrollTop = messages.scrollHeight;
    }
    function addMessage(text, kind) {
      var node = el("div", { class: "msg " + kind, text: text });
      messages.appendChild(node);
      scrollDown();
      return node;
    }

    // Answer bubble + numbered, clickable sources + latency / refusal indicator.
    function addAnswer(data) {
      var card = el("div", { class: "card" }, [el("div", { class: "msg bot", text: data.answer })]);

      var sources = data.sources || [];
      if (sources.length) {
        var list = el("ul", { class: "sources", "aria-label": "Sources" });
        sources.forEach(function (s) {
          var label = (s.heading || s.key) + " (" + s.key + ")";
          var url = sourceUrl(s.key);
          var item = el("li", {}, [el("span", { class: "n", text: "[" + s.n + "]" })]);
          if (url) {
            var link = el("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: label });
            item.appendChild(link);
          } else {
            item.appendChild(document.createTextNode(label));
          }
          list.appendChild(item);
        });
        card.appendChild(list);
      }

      var meta = el("div", { class: "meta" });
      if (isRefusal(data)) meta.appendChild(el("span", { class: "pill", text: "I don't know" }));
      var seconds = typeof data.latency_ms === "number" ? (data.latency_ms / 1000).toFixed(1) + " s" : "";
      var usage = data.usage || {};
      var tokens = usage.input_tokens || usage.output_tokens
        ? usage.input_tokens + " in / " + usage.output_tokens + " out tokens"
        : "no model call";
      meta.appendChild(el("span", { text: [seconds, tokens].filter(Boolean).join(" · ") }));
      card.appendChild(meta);

      messages.appendChild(card);
      scrollDown();
    }

    var busy = false;
    function ask(question) {
      question = (question || "").trim();
      if (!question || busy) return;
      if (question.length > MAX_QUESTION_CHARS) {
        addMessage("Please keep questions under " + MAX_QUESTION_CHARS + " characters.", "error");
        return;
      }
      setOpen(true);
      addMessage(question, "user");
      input.value = "";
      busy = true;
      input.disabled = true;
      send.disabled = true;
      var typing = el("div", { class: "typing", text: "Searching the docs..." });
      messages.appendChild(typing);
      scrollDown();

      fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(COLLECTION ? { question: question, collection: COLLECTION } : { question: question }),
      })
        .then(function (response) {
          return response
            .json()
            .catch(function () {
              return {};
            })
            .then(function (data) {
              if (!response.ok) throw new Error(data.error || "Request failed (" + response.status + ").");
              return data;
            });
        })
        .then(function (data) {
          typing.remove();
          addAnswer(data);
        })
        .catch(function (error) {
          typing.remove();
          var offline = error instanceof TypeError; // network failure or blocked by CORS
          addMessage(offline ? "Could not reach the service. Please try again." : error.message, "error");
        })
        .then(function () {
          busy = false;
          input.disabled = false;
          send.disabled = false;
          input.focus();
        });
    }

    send.addEventListener("click", function () {
      ask(input.value);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        ask(input.value);
      }
    });

    var header = panel.querySelector(".header");

    // Switch to another knowledge base (collection) and start a fresh conversation.
    function configure(options) {
      options = options || {};
      if (typeof options.collection === "string") COLLECTION = options.collection;
      if (typeof options.sourceUrlTemplate === "string") SOURCE_TEMPLATE = options.sourceUrlTemplate;
      if (typeof options.title === "string") {
        TITLE = options.title;
        header.textContent = TITLE;
        panel.setAttribute("aria-label", TITLE);
      }
      messages.textContent = ""; // old answers came from the previous collection
    }

    window.RagWidget = { ask: ask, configure: configure, open: function () { setOpen(true); } };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
