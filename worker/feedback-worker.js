/**
 * Feedback relay.
 *
 * The app posts a report here and this files it as an issue. It exists so the
 * credential lives on infrastructure the maintainer controls rather than
 * inside a program handed to other people, where anyone could read it out of
 * the binary.
 *
 * Two secrets are configured on the worker, never in the app:
 *   GITHUB_TOKEN  a fine-grained token limited to this one repository,
 *                 with Issues: write and nothing else
 *   GITHUB_REPO   "owner/name"
 *   APP_KEY       optional shared value the app sends; see the note below
 *
 * On abuse: the endpoint URL ships inside the app, so treat it as public.
 * APP_KEY only turns away drive-by scanners, since a determined person can
 * read it out of the binary too. The real protections are that the token can
 * do nothing except open issues on one repository, and that you can change or
 * switch off this worker instantly without touching a single installed copy.
 */

const ALLOWED_KINDS = ["Bug", "Suggestion", "Question"];
const MAX_MESSAGE = 4000;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clean(value, max) {
  return String(value == null ? "" : value).slice(0, max).trim();
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return json({ error: "post only" }, 405);
    }
    if (env.APP_KEY && request.headers.get("X-App-Key") !== env.APP_KEY) {
      return json({ error: "not allowed" }, 403);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "bad json" }, 400);
    }

    const message = clean(body.message, MAX_MESSAGE);
    if (!message) return json({ error: "empty message" }, 400);

    const kind = ALLOWED_KINDS.includes(body.kind) ? body.kind : "Feedback";
    const name = clean(body.name, 80);
    const version = clean(body.version, 32);
    const system = clean(body.system, 120);

    const title = `[${kind}] ${message.split("\n")[0].slice(0, 60)}`;
    const lines = [message, "", "---"];
    if (name) lines.push(`From: ${name}`);
    if (version) lines.push(`Version: ${version}`);
    if (system) lines.push(`System: ${system}`);

    const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "snippets-feedback",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        title,
        body: lines.join("\n"),
        labels: [kind.toLowerCase()],
      }),
    });

    if (!res.ok) {
      // Do not pass the upstream body back to the app; it can contain details
      // about the token and the repository that the sender has no business
      // seeing.
      return json({ error: "could not file the report" }, 502);
    }

    const issue = await res.json();
    return json({ ok: true, number: issue.number });
  },
};
