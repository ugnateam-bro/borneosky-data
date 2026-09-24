// Hourly trigger for the BorneoSky data pipeline.
// GitHub's own `schedule:` is best-effort and often late or skipped, so this
// Worker starts the workflow through the GitHub API instead. Cloudflare cron
// triggers are reliable and free. Runs that overlap queue behind each other
// (the workflow's concurrency group), so a duplicate trigger is harmless.

const REPO = 'ugnateam-bro/borneosky-data';
const WORKFLOW = 'ingest.yml';

async function dispatch(env) {
  if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN secret is not set');
  const res = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'borneosky-scheduler',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main' }),
    },
  );
  if (res.status !== 204) {
    // Never log the token; the response body names the problem.
    throw new Error(`GitHub returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  // Visiting the Worker's address shows it is alive; POST triggers a run by hand.
  async fetch(request, env) {
    if (request.method === 'POST') {
      try { await dispatch(env); return new Response('Workflow started\n'); }
      catch (e) { return new Response(String(e.message) + '\n', { status: 502 }); }
    }
    return new Response('borneosky-scheduler: hourly pipeline trigger\n');
  },
};
