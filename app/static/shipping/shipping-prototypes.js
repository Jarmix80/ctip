(() => {
  "use strict";

  const prototypeButtons = [...document.querySelectorAll("[data-prototype-target]")];
  const prototypeFrames = [...document.querySelectorAll("[data-prototype]")];
  const toast = document.getElementById("prototype-toast");
  let toastTimer = null;

  function activatePrototype(number, updateHash = true) {
    const selectedNumber = String(number);
    const targetFrame = prototypeFrames.find(
      (frame) => frame.dataset.prototype === selectedNumber,
    );
    if (!targetFrame) {
      return;
    }

    prototypeFrames.forEach((frame) => {
      const isSelected = frame === targetFrame;
      frame.classList.toggle("is-active", isSelected);
      frame.hidden = !isSelected;
    });
    prototypeButtons.forEach((button) => {
      const isSelected = button.dataset.prototypeTarget === selectedNumber;
      button.classList.toggle("is-active", isSelected);
      button.setAttribute("aria-pressed", String(isSelected));
    });

    if (updateHash) {
      window.history.replaceState(null, "", `#wariant-${selectedNumber}`);
    }
  }

  function activatePane(frame, paneName) {
    frame.querySelectorAll("[data-demo-view]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.demoView === paneName);
    });
    frame.querySelectorAll("[data-demo-pane]").forEach((pane) => {
      pane.classList.toggle("is-active", pane.dataset.demoPane === paneName);
    });
  }

  function showMockNotice() {
    if (!toast) {
      return;
    }
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 2200);
  }

  prototypeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activatePrototype(button.dataset.prototypeTarget);
    });
  });

  prototypeFrames.forEach((frame) => {
    frame.querySelectorAll("[data-demo-view]").forEach((button) => {
      button.addEventListener("click", () => {
        activatePane(frame, button.dataset.demoView);
      });
    });
    frame.querySelectorAll("[data-demo-action]").forEach((control) => {
      control.addEventListener("click", (event) => {
        event.preventDefault();
        showMockNotice();
      });
    });
  });

  const match = window.location.hash.match(/^#wariant-([1-7])$/);
  activatePrototype(match ? match[1] : "1", false);
})();
