(function () {
  "use strict";
  function toast(msg, type) {
    var root = document.getElementById("toastRoot");
    var el = document.createElement("div");
    el.className = "toast " + (type || "");
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 2600);
  }

  function closeModal() {
    var root = document.getElementById("modalRoot");
    root.classList.add("hidden");
    root.innerHTML = "";
  }

  function modal(opts) {
    closeModal();
    var root = document.getElementById("modalRoot");
    root.classList.remove("hidden");
    root.onclick = function (e) {
      if (e.target === root) closeModal();
    };
    var box = document.createElement("div");
    box.className = "modal" + (opts.wide ? " wide" : "");
    var h = document.createElement("h3");
    if (opts.titleHTML) {
      h.innerHTML = opts.titleHTML;
    } else {
      h.textContent = opts.title || "";
    }
    box.appendChild(h);
    if (opts.bodyHTML) {
      var body = document.createElement("div");
      body.innerHTML = opts.bodyHTML;
      box.appendChild(body);
    }
    root.appendChild(box);
    if (opts.onOpen) opts.onOpen(box);
    return box;
  }

  function confirm(title, text, onOk) {
    modal({
      title: title,
      bodyHTML: "<p class='desc'>" + text + "</p>" +
        "<div class='acts'>" +
        "<button class='btn' id='mCancel'>取消</button>" +
        "<button class='btn mastered' id='mOk'>确认</button></div>",
      onOpen: function (box) {
        box.querySelector("#mCancel").onclick = closeModal;
        box.querySelector("#mOk").onclick = function () {
          closeModal();
          onOk();
        };
      }
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModal();
  });

  window.UI = { toast: toast, modal: modal, closeModal: closeModal, confirm: confirm };
})();
