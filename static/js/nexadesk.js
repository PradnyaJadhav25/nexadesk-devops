"use strict";

const menuButton = document.querySelector(".mobile-menu");
const sidebar = document.querySelector(".sidebar");

if (menuButton && sidebar) {
  menuButton.addEventListener("click", () => {
    const open = sidebar.classList.toggle("is-open");
    menuButton.setAttribute("aria-expanded", String(open));
  });

  document.addEventListener("click", (event) => {
    if (
      sidebar.classList.contains("is-open") &&
      !sidebar.contains(event.target) &&
      !menuButton.contains(event.target)
    ) {
      sidebar.classList.remove("is-open");
      menuButton.setAttribute("aria-expanded", "false");
    }
  });
}
