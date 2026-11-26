// Social kit: the daily money-flow video, cover, picture post and captions made by social/make_kit.js.
// Kept in KV for 8 days, admin only. Nothing here goes to the public repo.
import { alertsGet, sendMail } from "./alerts.js";

const TTL = 60 * 60 * 24 * 8, IDX = "soc:index";
const TYPES = { video: ["video/mp4", "mp4"], cover: ["image/jpeg", "jpg"], post: ["image/jpeg", "jpg"], tut: ["video/mp4", "mp4"] };
// Files arrive one per request as raw bytes (?d=DATE&f=video|cover|post): no decoding, so it stays within CPU limits.
export async function socialFileIngest(env, req, url) {
  const d = url.searchParams.get("d") || "", f = url.searchParams.get("f") || "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d) || !TYPES[f]) return { error: "Bad file." };
  const buf = await req.arrayBuffer();
  if (!buf.byteLength || buf.byteLength > 24e6) return { error: "Empty or too big." };
  await env.USERS.put(`soc:${d}:${f}`, buf, { expirationTtl: TTL });
  return { ok: true, bytes: buf.byteLength };
}
// The captions and details come last; saving them lists the day and sends the email.
export async function socialIngest(env, body) {
  const m = body.meta || {}, date = String(m.date || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return { error: "No date." };
  await env.USERS.put(`soc:${date}:meta`, JSON.stringify(m), { expirationTtl: TTL });
  const idx = ((await env.USERS.get(IDX, "json")) || []).filter(d => d !== date);
  idx.unshift(date);
  await env.USERS.put(IDX, JSON.stringify(idx.sort().reverse().slice(0, 8)));
  const { email } = await alertsGet(env);
  const held = (m.held || []).length;
  const link = "https://tradeready.in/#admin-social";
  const subject = held ? `⚠️ Social kit for ${date} held: please check` : `📣 Today's social kit is ready: ${m.headline || ""}`;
  const text = `${m.headline || ""}\n\n${held ? "Held because: " + m.held.join(" ") + "\n\n" : ""}Open the kit (video, pictures, captions): ${link}`;
  const html = `<div style="font-family:Inter,Arial,sans-serif;max-width:520px">
    <h2 style="margin:0 0 6px">${held ? "⚠️ Kit held for a check" : "📣 Today's social kit is ready"}</h2>
    <p style="font-size:16px;margin:0 0 14px"><b>${esc(m.headline || "")}</b> · F&amp;O market ${m.mkt > 0 ? "+" : ""}${m.mkt}%</p>
    ${held ? `<p style="background:#fff4e5;padding:10px 12px;border-radius:8px">${m.held.map(esc).join("<br>")}</p>` : ""}
    <p><a href="${link}" style="display:inline-block;background:#1e1b4b;color:#fff;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">Open the kit</a></p>
    <p style="color:#666;font-size:13px">Video, cover, picture post and captions for Instagram, YouTube, X and WhatsApp. Kept for 7 days.</p></div>`;
  const mail = email ? await sendMail(env, email, subject, html, text) : "no email saved";
  return { ok: true, date, mail };
}

export async function socialList(env) {
  const idx = (await env.USERS.get(IDX, "json")) || [];
  const metas = await Promise.all(idx.map(d => env.USERS.get(`soc:${d}:meta`, "json").catch(() => null)));
  return { days: metas.filter(Boolean) };
}

export async function socialFile(env, url) {
  const d = url.searchParams.get("d") || "", f = url.searchParams.get("f") || "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d) || !TYPES[f]) return new Response("Not found", { status: 404 });
  const buf = await env.USERS.get(`soc:${d}:${f}`, "arrayBuffer");
  if (!buf) return new Response("Gone (kits are kept 7 days)", { status: 404 });
  const h = { "content-type": TYPES[f][0], "cache-control": "private, max-age=3600", "accept-ranges": "none" };
  if (url.searchParams.get("dl")) h["content-disposition"] = `attachment; filename="tradeready-${f}-${d}.${TYPES[f][1]}"`;
  return new Response(buf, { headers: h });
}

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
