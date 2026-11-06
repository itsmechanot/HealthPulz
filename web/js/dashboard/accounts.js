// BHU Accounts screen: the RHU is the only one who gives a BHU its password. A password is shown
// once, when it is given. After that the server only has its hash, so losing it means giving a new one.

import * as api from "../common/api.js";
import { el } from "../common/dom.js";

const $ = (id) => document.getElementById(id);
const MIN_PASSWORD_LENGTH = 8;

const postJson = (url, body) =>
  api.fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export function createAccountsScreen() {
  let editing = null;

  async function show() {
    try {
      render(await api.fetchJson("/api/accounts"));
      $("accounts-error").textContent = "";
    } catch (error) {
      $("accounts-error").textContent = `Could not load the accounts. (${error.message})`;
    }
  }

  function render(accounts) {
    const missing = accounts.filter((a) => !a.assigned).length;
    $("accounts-summary").textContent = missing
      ? `${accounts.length - missing} of ${accounts.length} BHUs have a password. A BHU without one cannot log in.`
      : `All ${accounts.length} BHUs have a password.`;
    $("assign-missing-btn").hidden = missing === 0;
    $("assign-missing-btn").textContent = `Generate for the other ${missing}`;
    $("accounts-list").replaceChildren(
      ...accounts.map((account) =>
        el(
          "li",
          { className: "account-row" },
          el("strong", { textContent: account.name }),
          el("span", {
            className: "badge",
            dataset: { level: account.assigned ? "OK" : "ALERT" },
            textContent: account.assigned ? "Has password" : "No password yet",
          }),
          el("span", { className: "muted small", textContent: when(account.password_set_at) }),
          el("button", {
            className: "btn btn-ghost btn-small",
            type: "button",
            textContent: account.assigned ? "Change password" : "Give password",
            onclick: () => openDialog(account),
          }),
        ),
      ),
    );
  }

  function openDialog(account) {
    editing = account;
    $("password-title").textContent = `Password for Brgy. ${account.name}`;
    $("password-warning").hidden = !account.assigned;
    $("password-value").value = "";
    $("password-error").textContent = "";
    $("password-dialog").showModal();
  }

  async function save(event) {
    event.preventDefault();
    const password = $("password-value").value;
    if (password && password.length < MIN_PASSWORD_LENGTH) {
      $("password-error").textContent = `Use at least ${MIN_PASSWORD_LENGTH} characters, or leave it blank to generate one.`;
      return;
    }
    $("password-save").disabled = true;
    try {
      const given = await postJson(`/api/accounts/${editing.barangay_id}/password`, { password: password || null });
      $("password-dialog").close();
      showPasswords([given]);
    } catch (error) {
      $("password-error").textContent = `Could not save the password. (${error.message})`;
    } finally {
      $("password-save").disabled = false;
    }
  }

  async function assignMissing() {
    const button = $("assign-missing-btn");
    button.disabled = true;
    try {
      showPasswords(await postJson("/api/accounts/assign-missing"));
    } catch (error) {
      $("accounts-error").textContent = `Could not generate the passwords. (${error.message})`;
    } finally {
      button.disabled = false;
    }
  }

  /** The only time a password can be seen, so this stays open until the RHU presses Done. */
  function showPasswords(given) {
    $("given-list").replaceChildren(
      ...given.map((account) =>
        el(
          "li",
          { className: "given-row" },
          el("span", { textContent: `Brgy. ${account.name}` }),
          el("code", { textContent: account.password }),
        ),
      ),
    );
    $("given-dialog").showModal();
    show();
  }

  $("assign-missing-btn").addEventListener("click", assignMissing);
  $("password-form").addEventListener("submit", save);
  $("password-cancel").addEventListener("click", () => $("password-dialog").close());
  $("given-print").addEventListener("click", () => window.print());
  $("given-done").addEventListener("click", () => {
    $("given-list").replaceChildren();
    $("given-dialog").close();
  });
  $("given-dialog").addEventListener("cancel", (event) => event.preventDefault());

  return { show };
}

function when(timestamp) {
  if (!timestamp) return "";
  return `Given ${new Date(timestamp).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
}
