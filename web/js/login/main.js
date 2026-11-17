// Login page: the server sends each account to its own side.

import * as api from "../common/api.js";
import { createCombobox } from "../common/combobox.js";
import { applyPlace } from "../common/place.js";

const $ = (id) => document.getElementById(id);

let chosen = null;

main();

async function main() {
  let config;
  try {
    config = await api.getConfig();
  } catch (error) {
    showError(`Cannot reach the RHU server. Check the Wi-Fi and reload. (${error.message})`);
    return;
  }

  const place = applyPlace(config.municipality);
  const rhuOption = { username: "RHU", name: place.rhu_name };
  const barangays = [...config.barangays]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((b) => ({ username: b.barangay_id, name: b.name }));

  createCombobox({
    input: $("unit-input"),
    listbox: $("unit-list"),
    options: [rhuOption, ...barangays],
    labelOf: (option) => option.name,
    onSelect: (option) => {
      chosen = option;
      showError("");
    },
  });

  $("show-password").addEventListener("click", () => {
    const hidden = $("password-input").type === "password";
    $("password-input").type = hidden ? "text" : "password";
    $("show-password").textContent = hidden ? "Hide" : "Show";
    $("show-password").setAttribute("aria-pressed", hidden);
  });

  $("login-form").addEventListener("submit", logIn);
  $("login-btn").disabled = false;
}

async function logIn(event) {
  event.preventDefault();
  if (!chosen) return showError("Choose your health unit from the list.");
  if (!$("password-input").value) return showError("Enter your password.");

  $("login-btn").disabled = true;
  showError("");
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: chosen.username, password: $("password-input").value }),
    });
    const body = await res.json().catch(() => null);
    if (res.ok) {
      location.replace(body.home);
      return;
    }
    showError(typeof body?.detail === "string" ? body.detail : "The server refused this login. Please try again.");
    $("password-input").select();
  } catch {
    showError("Cannot reach the RHU server. Check the Wi-Fi and try again.");
  }
  $("login-btn").disabled = false;
}

function showError(message) {
  $("login-error").textContent = message;
}
