# Feedback relay setup

One-time setup, about fifteen minutes, free. Do this once and every installed
copy of the app can send feedback without anyone needing an account.

The app posts a report to this relay, and the relay files it as an issue. The
credential lives here rather than inside the app, because anyone who downloads
an executable can read a token out of it.

## 1. Create the token

1. Go to <https://github.com/settings/personal-access-tokens/new>
2. **Token name**: `snippets-feedback`
3. **Expiration**: whatever you are willing to renew. A year is reasonable.
4. **Repository access**: *Only select repositories*, then pick `snippets`
5. **Permissions**: expand *Repository permissions*, set **Issues** to
   **Read and write**. Leave every other permission alone.
6. Generate it and copy the value. You will not see it again.

Issues on one repository is all this token can ever do. If it did leak, the
worst case is unwanted issues on that one repo, and you can revoke it in a
click.

## 2. Deploy the worker

1. Sign up at <https://workers.cloudflare.com>. The free plan covers 100,000
   requests a day and does not ask for a card.
2. **Compute (Workers)** then **Create**, choose **Start from Hello World**,
   name it `snippets-feedback`, and deploy.
3. **Edit code**, delete what is there, paste in `feedback-worker.js` from
   this folder, and deploy again.
4. Open **Settings**, then **Variables and Secrets**, and add three:

   | Name | Type | Value |
   |---|---|---|
   | `GITHUB_TOKEN` | Secret | the token from step 1 |
   | `GITHUB_REPO` | Text | `vinay121314/snippets` |
   | `APP_KEY` | Secret | any random string you invent |

   Encrypting `GITHUB_TOKEN` as a Secret means it cannot be read back out of
   the dashboard afterwards.
5. Copy the worker URL, which looks like
   `https://snippets-feedback.<your-subdomain>.workers.dev`

## 3. Point the app at it

In [`feedback.py`](../feedback.py), set:

```python
ENDPOINT = "https://snippets-feedback.<your-subdomain>.workers.dev"
APP_KEY  = "the same random string you used above"
```

Then run `BUILD.bat` and publish the new version.

You can also override it per machine without rebuilding, using
`HKLM\Software\Snippets` value `FeedbackUrl`, which is how a managed
deployment would point it somewhere internal.

## 4. Check it works

Open the app, choose **Send feedback...** from the tray, write something and
press **Send**. An issue should appear on the repository within a second or
two.

If it does not, the app keeps the report in
`%APPDATA%\SnippetsApp\outbox\` and retries later, so nothing is lost while
you sort the setup out. `%APPDATA%\SnippetsApp\snippets.log` records what went
wrong.

## If it ever gets abused

The endpoint URL is inside the app, so treat it as public. `APP_KEY` only
turns away automated scanners, since someone determined can read it out of the
binary as well.

What actually protects you is that the token can do nothing but open issues on
one repository, and that you can edit or disable the worker instantly from the
dashboard. Installed copies simply start queueing their reports and stop
bothering you, with no new release needed.
