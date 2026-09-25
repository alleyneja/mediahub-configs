# Standard: filing issues in other projects' GitHubs (2026-09-25)

When a problem is in someone else's software (Sonarr, Threadfin, Immich, mergerfs, a vendor like UGREEN...), we
report it upstream. The report goes out under Jay's name, so it has to be accurate, respectful of maintainers' time,
and must never leak anything about the homelab.

## Before filing
1. **Search first.** Check open *and* closed issues and discussions for the same problem. If one exists, add a short
   comment with our versions and anything new, or a 👍. Don't open a duplicate.
2. **Confirm it's theirs.** Reproduce it on the current release if we reasonably can, and rule out our own config.
   Our evidence standard applies: 2-3 independent signals, a control where possible.
3. **Read their rules.** Use the project's issue template and CONTRIBUTING guide. Some want Discussions or a forum
   instead of Issues. Some have AI-content policies; follow them.

## What goes in
- **One problem per issue**, with a specific title ("Import stalls on anthology editions", not "bug").
- **Exact versions**: app, image tag, OS/kernel, relevant hardware (`scripts/issue-env.sh` output, trimmed).
- **Minimal reproduction steps**, expected vs. actual behaviour.
- **Logs**, only the relevant lines.
- **What we already tried** and ruled out.
- **Disclose AI assistance**: "Investigated with the help of an AI assistant (Claude); findings verified by me." Honest,
  and many projects now ask for it.

## What never goes in (redact before posting)
- Secrets: API keys, tokens, passwords, webhook URLs, cookies. Also check screenshots.
- Internal addresses and names: LAN/Tailscale IPs, hostnames, domain names, usernames, email addresses.
  Use placeholders like `<nas-ip>` or `host-a`.
- Personal data: media titles tied to people, user names, locations.
- Links to our private tracker. Those are dead links for everyone else. Just mention our issue number in our own notes.

## Workflow
1. Claude drafts the report in our private issue (a comment, or a file linked from it), fully redacted.
2. **Jay reviews and submits it himself** (or explicitly tells Claude to submit). Nothing is posted under Jay's name
   without that approval.
3. Put the upstream link on our private issue, labelled `status:blocked` if we're waiting on them.
4. When upstream responds or ships a fix, update our issue. Close it once the fix is deployed and verified.
