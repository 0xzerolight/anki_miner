#!/usr/bin/env node
// Regenerate CONTRIBUTORS.md from issue, discussion, and pull-request
// participants. Run from the workflow at .github/workflows/contributors.yml,
// or locally with:
//   GITHUB_TOKEN=$(gh auth token) \
//   GITHUB_REPOSITORY=0xzerolight/anki_miner \
//   node scripts/update_contributors.mjs
//
// No external dependencies. Node 20+ (global fetch).

import { appendFile, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";

const TOKEN = process.env.GITHUB_TOKEN;
const REPO = process.env.GITHUB_REPOSITORY;
if (!TOKEN || !REPO) {
  console.error("GITHUB_TOKEN and GITHUB_REPOSITORY env vars are required.");
  process.exit(1);
}
const [OWNER, NAME] = REPO.split("/");

const OUTPUT_PATH = "CONTRIBUTORS.md";
const START_MARKER = "<!-- contributors:start -->";
const END_MARKER = "<!-- contributors:end -->";
const COLUMNS = 8;
const AVATAR_SIZE = 80;

const DEFAULT_PREAMBLE = `# Contributors

Thanks to everyone who has helped shape \`anki_miner\` by filing issues, joining
discussions, or opening pull requests. See also the [Special Thanks section in
the README](README.md#special-thanks) for exceptional contributions.

This list is generated automatically — see [.github/workflows/contributors.yml](.github/workflows/contributors.yml).

${START_MARKER}
${END_MARKER}
`;

async function gql(query, variables) {
  const res = await fetch("https://api.github.com/graphql", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "anki_miner-contributors-script",
    },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) {
    throw new Error(`GraphQL HTTP ${res.status}: ${await res.text()}`);
  }
  const json = await res.json();
  if (json.errors) {
    throw new Error(`GraphQL errors: ${JSON.stringify(json.errors)}`);
  }
  return json.data;
}

function addActor(map, actor) {
  // actor can be null for deleted users / ghosted accounts. Skip those.
  if (!actor || !actor.login) return;
  // GraphQL exposes bots as a distinct __typename. REST-style "[bot]" suffix
  // is NOT present on Actor.login here, so login-string heuristics miss
  // dependabot / github-actions — typename is the canonical signal.
  if (actor.__typename === "Bot") return;
  if (map.has(actor.login)) return;
  map.set(actor.login, {
    login: actor.login,
    avatarUrl: actor.avatarUrl,
    url: `https://github.com/${actor.login}`,
  });
}

const ACTOR_FRAGMENT = `
  __typename
  login
  avatarUrl(size: ${AVATAR_SIZE})
`;

async function collectIssuesAndPRs(map) {
  // issues and pullRequests share the same shape for our purposes.
  for (const field of ["issues", "pullRequests"]) {
    const statesArg =
      field === "pullRequests"
        ? "states: [OPEN, CLOSED, MERGED]"
        : "states: [OPEN, CLOSED]";
    let cursor = null;
    for (;;) {
      const data = await gql(
        `query($owner:String!,$name:String!,$cursor:String){
          repository(owner:$owner,name:$name){
            ${field}(first:100, after:$cursor, ${statesArg}){
              pageInfo { hasNextPage endCursor }
              nodes {
                number
                author { ${ACTOR_FRAGMENT} }
                comments(first:100){
                  pageInfo { hasNextPage endCursor }
                  nodes { author { ${ACTOR_FRAGMENT} } }
                }
              }
            }
          }
        }`,
        { owner: OWNER, name: NAME, cursor },
      );
      const conn = data.repository[field];
      for (const node of conn.nodes) {
        addActor(map, node.author);
        for (const c of node.comments.nodes) addActor(map, c.author);
        if (node.comments.pageInfo.hasNextPage) {
          await drainComments(map, field, node.number, node.comments.pageInfo.endCursor);
        }
      }
      if (!conn.pageInfo.hasNextPage) break;
      cursor = conn.pageInfo.endCursor;
    }
  }
}

async function drainComments(map, field, number, startCursor) {
  // field is "issues" or "pullRequests"; the GraphQL parent type differs.
  const parent = field === "issues" ? "issue" : "pullRequest";
  let cursor = startCursor;
  for (;;) {
    const data = await gql(
      `query($owner:String!,$name:String!,$number:Int!,$cursor:String){
        repository(owner:$owner,name:$name){
          ${parent}(number:$number){
            comments(first:100, after:$cursor){
              pageInfo { hasNextPage endCursor }
              nodes { author { ${ACTOR_FRAGMENT} } }
            }
          }
        }
      }`,
      { owner: OWNER, name: NAME, number, cursor },
    );
    const conn = data.repository[parent].comments;
    for (const c of conn.nodes) addActor(map, c.author);
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
}

async function collectDiscussions(map) {
  // GraphQL caps a query at 500k worst-case nodes. discussions × comments ×
  // replies multiplies, so keep inner page sizes small and paginate when needed.
  let cursor = null;
  for (;;) {
    const data = await gql(
      `query($owner:String!,$name:String!,$cursor:String){
        repository(owner:$owner,name:$name){
          discussions(first:50, after:$cursor){
            pageInfo { hasNextPage endCursor }
            nodes {
              number
              author { ${ACTOR_FRAGMENT} }
              comments(first:50){
                pageInfo { hasNextPage endCursor }
                nodes {
                  id
                  author { ${ACTOR_FRAGMENT} }
                  replies(first:20){
                    pageInfo { hasNextPage endCursor }
                    nodes { author { ${ACTOR_FRAGMENT} } }
                  }
                }
              }
            }
          }
        }
      }`,
      { owner: OWNER, name: NAME, cursor },
    );
    // Discussions may not be enabled on the repo; GraphQL returns null then.
    const conn = data.repository.discussions;
    if (!conn) return;
    for (const node of conn.nodes) {
      addActor(map, node.author);
      for (const c of node.comments.nodes) {
        addActor(map, c.author);
        for (const r of c.replies.nodes) addActor(map, r.author);
        if (c.replies.pageInfo.hasNextPage) {
          await drainDiscussionReplies(map, c.id, c.replies.pageInfo.endCursor);
        }
      }
      if (node.comments.pageInfo.hasNextPage) {
        await drainDiscussionComments(map, node.number, node.comments.pageInfo.endCursor);
      }
    }
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
}

async function drainDiscussionComments(map, number, startCursor) {
  let cursor = startCursor;
  for (;;) {
    const data = await gql(
      `query($owner:String!,$name:String!,$number:Int!,$cursor:String){
        repository(owner:$owner,name:$name){
          discussion(number:$number){
            comments(first:50, after:$cursor){
              pageInfo { hasNextPage endCursor }
              nodes {
                id
                author { ${ACTOR_FRAGMENT} }
                replies(first:20){
                  pageInfo { hasNextPage endCursor }
                  nodes { author { ${ACTOR_FRAGMENT} } }
                }
              }
            }
          }
        }
      }`,
      { owner: OWNER, name: NAME, number, cursor },
    );
    const conn = data.repository.discussion.comments;
    for (const c of conn.nodes) {
      addActor(map, c.author);
      for (const r of c.replies.nodes) addActor(map, r.author);
      if (c.replies.pageInfo.hasNextPage) {
        await drainDiscussionReplies(map, c.id, c.replies.pageInfo.endCursor);
      }
    }
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
}

async function drainDiscussionReplies(map, commentId, startCursor) {
  let cursor = startCursor;
  for (;;) {
    const data = await gql(
      `query($id:ID!,$cursor:String){
        node(id:$id){
          ... on DiscussionComment {
            replies(first:50, after:$cursor){
              pageInfo { hasNextPage endCursor }
              nodes { author { ${ACTOR_FRAGMENT} } }
            }
          }
        }
      }`,
      { id: commentId, cursor },
    );
    const conn = data.node.replies;
    for (const r of conn.nodes) addActor(map, r.author);
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
}

function renderTable(contributors) {
  if (contributors.length === 0) {
    return "_No contributors yet._";
  }
  const cellWidth = (100 / COLUMNS).toFixed(2);
  const rows = [];
  for (let i = 0; i < contributors.length; i += COLUMNS) {
    const cells = contributors.slice(i, i + COLUMNS).map(
      (c) =>
        `    <td align="center" valign="top" width="${cellWidth}%">` +
        `<a href="${c.url}">` +
        `<img src="${c.avatarUrl}" width="${AVATAR_SIZE}" height="${AVATAR_SIZE}" alt="@${c.login}"/>` +
        `<br/><sub><b>${c.login}</b></sub></a>` +
        `</td>`,
    );
    rows.push("  <tr>\n" + cells.join("\n") + "\n  </tr>");
  }
  return `<table>\n${rows.join("\n")}\n</table>`;
}

function spliceBetweenMarkers(existing, block) {
  const start = existing.indexOf(START_MARKER);
  const end = existing.indexOf(END_MARKER);
  if (start === -1 || end === -1 || end < start) {
    throw new Error(
      `Markers not found in ${OUTPUT_PATH}; expected ${START_MARKER} and ${END_MARKER}.`,
    );
  }
  return (
    existing.slice(0, start + START_MARKER.length) +
    "\n" +
    block +
    "\n" +
    existing.slice(end)
  );
}

function extractExistingLogins(text) {
  // Parse logins out of the rendered table. We control the render format —
  // every cell has `alt="@<login>"`. Robust enough; if the markers are missing
  // or the table block is empty we return an empty set and every contributor
  // looks new (correct behavior on first-run).
  const start = text.indexOf(START_MARKER);
  const end = text.indexOf(END_MARKER);
  if (start === -1 || end === -1 || end < start) return new Set();
  const block = text.slice(start, end);
  const logins = new Set();
  for (const match of block.matchAll(/alt="@([^"]+)"/g)) {
    logins.add(match[1]);
  }
  return logins;
}

async function emitGithubOutput(name, value) {
  // No-op when running locally without Actions env.
  const path = process.env.GITHUB_OUTPUT;
  if (!path) return;
  await appendFile(path, `${name}=${value}\n`);
}

async function main() {
  const map = new Map();
  await collectIssuesAndPRs(map);
  await collectDiscussions(map);

  const contributors = [...map.values()].sort((a, b) =>
    a.login.toLowerCase().localeCompare(b.login.toLowerCase()),
  );

  console.log(`Collected ${contributors.length} contributors after bot filter.`);

  const existing = existsSync(OUTPUT_PATH)
    ? await readFile(OUTPUT_PATH, "utf8")
    : DEFAULT_PREAMBLE;
  const previousLogins = extractExistingLogins(existing);
  const newLogins = contributors
    .map((c) => c.login)
    .filter((login) => !previousLogins.has(login));

  const block = renderTable(contributors);
  const next = spliceBetweenMarkers(existing, block);
  await writeFile(OUTPUT_PATH, next);
  console.log(`Wrote ${OUTPUT_PATH}.`);

  if (newLogins.length > 0) {
    console.log(`New contributors this run: ${newLogins.join(", ")}`);
  } else {
    console.log("No new contributors this run.");
  }

  // Comma-joined for the workflow to consume. Workflow caps display length and
  // formats with @-prefixes — script just exports the raw list.
  await emitGithubOutput("new_logins", newLogins.join(","));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
