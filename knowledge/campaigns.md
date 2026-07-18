# Campaigns & post-aware comments

When a Reel/post caption asks people to comment a keyword (e.g. TIKLANISH, ANALIZ):

1. Fetch and cache the media caption (Mongo `media` collection).
2. If the comment matches the CTA keyword → public thank-you + automatic DM (campaign flow).
3. Otherwise → Gemini replies with full post context (caption, intent, author, memory).

Stored campaigns (Mongo `campaigns`) can override auto-detection with custom:
- trigger keywords
- public reply text
- DM text / PDF URL
- ask name/phone / offer consultation flags
