document.addEventListener("DOMContentLoaded", function () {
  const groups = document.querySelectorAll(".top-menu-group");

  groups.forEach(function (group) {
    const button = group.querySelector(".top-menu-btn");
    const dropdown = group.querySelector(".top-menu-dropdown");

    if (!button || !dropdown) return;

    button.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();

      const isOpen = group.classList.contains("open");

      groups.forEach(function (other) {
        other.classList.remove("open");

        const otherDropdown = other.querySelector(".top-menu-dropdown");
        if (otherDropdown) {
          otherDropdown.style.removeProperty("position");
          otherDropdown.style.removeProperty("top");
          otherDropdown.style.removeProperty("right");
          otherDropdown.style.removeProperty("left");
        }
      });

      if (!isOpen) {
        const rect = button.getBoundingClientRect();

        dropdown.style.setProperty("position", "fixed", "important");
        dropdown.style.setProperty("top", (rect.bottom + 3) + "px", "important");
        dropdown.style.setProperty("right", "auto", "important");

        let left = rect.right - 210;

        if (left < 10) left = 10;
        if (left + 210 > window.innerWidth - 10) {
          left = window.innerWidth - 220;
        }

        dropdown.style.setProperty("left", left + "px", "important");
        group.classList.add("open");
      }
    });
  });

  document.addEventListener("click", function () {
    groups.forEach(function (group) {
      group.classList.remove("open");

      const dropdown = group.querySelector(".top-menu-dropdown");
      if (dropdown) {
        dropdown.style.removeProperty("position");
        dropdown.style.removeProperty("top");
        dropdown.style.removeProperty("right");
        dropdown.style.removeProperty("left");
      }
    });
  });
});
