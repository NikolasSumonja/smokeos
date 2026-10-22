document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const err = document.getElementById("err");
  err.textContent = "";
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SmokeOS": "1" },
      body: JSON.stringify(Object.fromEntries(fd)),
    });
    if (r.ok) { location.href = "/"; return; }
    const j = await r.json().catch(() => ({}));
    err.textContent = j.detail || "Login failed";
  } catch { err.textContent = "Can't reach the server"; }
});
