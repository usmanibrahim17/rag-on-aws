// Wires the example-question buttons to the chat widget (window.RagWidget, from widget.js).
// Kept in its own file because the site's Content-Security-Policy forbids inline scripts.
(function () {
  "use strict";
  document.querySelectorAll("button[data-q]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (window.RagWidget) window.RagWidget.ask(button.getAttribute("data-q"));
    });
  });
})();
