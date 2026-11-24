// Demo page behaviour: the knowledge-base tabs and the example-question buttons.
// Kept in its own file because the site's Content-Security-Policy forbids inline scripts.
(function () {
  "use strict";

  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));

  function selectTab(tab) {
    tabs.forEach(function (other) {
      var active = other === tab;
      other.setAttribute("aria-selected", active ? "true" : "false");
      other.tabIndex = active ? 0 : -1;
      document.getElementById(other.getAttribute("aria-controls")).hidden = !active;
    });
    // Point the chat widget at this tab's collection and start a clean conversation.
    if (window.RagWidget) {
      window.RagWidget.configure({
        collection: tab.getAttribute("data-collection"),
        title: tab.getAttribute("data-title"),
        sourceUrlTemplate: tab.getAttribute("data-source-url-template"),
      });
    }
  }

  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () {
      selectTab(tab);
    });
    // Arrow keys move between tabs, as in the WAI-ARIA tabs pattern.
    tab.addEventListener("keydown", function (event) {
      var next = null;
      if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
      if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
      if (next) {
        event.preventDefault();
        next.focus();
        selectTab(next);
      }
    });
  });

  document.querySelectorAll("button[data-q]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (window.RagWidget) window.RagWidget.ask(button.getAttribute("data-q"));
    });
  });

  // The widget starts on the first tab's collection (see its data-* attributes).
})();
