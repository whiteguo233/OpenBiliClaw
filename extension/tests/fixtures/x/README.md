# X (Twitter) capture fixtures — SYNTHETIC PLACEHOLDERS

> **These are synthetic-but-realistic placeholders.** They are hand-authored
> to match the documented shapes of X's GraphQL mutation request bodies and
> the REST follow call, NOT captured from a live x.com session.
>
> **TODO (cookie smoke / Task 4 follow-up):** replace each file with a REAL
> capture taken from DevTools while logged into x.com — perform the action
> (like / bookmark / retweet / reply / follow / open-tweet), copy the GraphQL
> request **payload** + response, and overwrite the matching fixture. The
> hashed `queryId` segment in the URL rotates every ~2-4 weeks, so the parser
> matches on the GraphQL **operation name**, never the queryId.

Each fixture is a `CapturedXRequest` as the MAIN-world tap observes it:

```jsonc
{
  "url": "https://x.com/i/api/graphql/<queryId>/<OperationName>",
  "requestBody": "<raw request body string, usually JSON or form-encoded>",
  "responseBody": "<raw response body string, may be empty>"
}
```

`parseXMutation(captured)` reads the operation name from the URL, then walks
`requestBody` (and `responseBody` as a fallback) depth-first to recover the
target tweet id / user id, returning `{type, tweet_id}` (or `{type, user_id}`
for follow).

| Fixture | Operation | Event |
| --- | --- | --- |
| `favorite_tweet.json` | `FavoriteTweet` | `like` |
| `create_bookmark.json` | `CreateBookmark` | `favorite` |
| `create_retweet.json` | `CreateRetweet` | `share` |
| `reply_create_tweet.json` | `CreateTweet` (with `reply`) | `comment` |
| `follow.json` | REST `/1.1/friendships/create.json` | `follow` |
| `tweet_detail.json` | `TweetDetail` | `view` |
