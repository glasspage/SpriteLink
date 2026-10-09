SpriteLink is an end-to-end encrypted messaging program built around chatrooms.
It uses ntfy-compatible servers as encrypted message relays, allowing users to communicate securely without a dedicated backend.

SpriteLink is designed around the limitations of the public ntfy server, including its 4 KB message size limit, 250-message daily publish limit, and 12-hour server retention period. 
Ideally, *SpriteLink should be left running* so it can receive and save messages before they expire from the server.

Video links from YouTube, Vimeo, Dailymotion, and Streamable appear as click-to-play thumbnails, as do direct MP4, WebM, MOV, and M4V links from trusted websites. Thumbnails use the image embed size limit, with a centered play button and a black border. Hover to see the website, title, author, and duration when available.

YouTube, Vimeo, and direct videos use SpriteLink's themed playback controls. Other providers retain their embedded controls. YouTube chooses playback quality automatically; its API no longer supports requesting a target resolution. Provider restrictions, unavailable codecs, or embedding failures can be handled with **Open in Browser**. Existing GIF-style looping videos remain silent and autoplay inline.

Videos open inside the main SpriteLink window at any window size. **Pop-out** moves playback into a separate nonmodal window and removes the main-window overlay, retaining the video link in its footer. **Mini player** keeps a compact 16:9 video at the top right of the chat log. Both detached modes continue playing across chatroom switches. Mode changes preserve playback. Fullscreen fills the screen with square corners and the same SpriteLink controls; Escape restores the previous view. Scroll over the volume button or slider to adjust volume in 10% increments; scrolling the seek bar does not change playback position.

The video stays centered over black while resizing and retains its last frame until the renderer updates. YouTube and Vimeo render on a separate, fixed 1080p surface so resizing and switching player modes do not change the main window's compositor. Captured frames are displayed at up to 60 fps, with less frequent capture while paused. YouTube uses 25% browser zoom to shrink remaining controls and logos while the video still fills the player. Its extra overlays are also hidden by following the video layer and refreshing suppression when the embed changes; provider errors and ad controls remain visible. Suppression is scoped to YouTube embeds and is best-effort because YouTube can change its interface.

DISCLAIMER: The code in this repository was created with assistance from AI tools. I made the architecture, design and implementation decisions; AI was used to write the code based on them.
