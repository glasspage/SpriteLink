SpriteLink is an end-to-end encrypted messaging program built around chatrooms.
It uses ntfy-compatible servers as encrypted message relays, allowing users to communicate securely without a dedicated backend.

SpriteLink is designed around the limitations of the public ntfy server, including its 4 KB message size limit, 250-message daily publish limit, and 12-hour server retention period. 
Ideally, *SpriteLink should be left running* so it can receive and save messages before they expire from the server.

Video links from YouTube, Vimeo, Dailymotion, and Streamable appear as click-to-play thumbnails, as do direct MP4, WebM, MOV, and M4V links from trusted websites. Thumbnails use the image embed size limit, with a centered play button and a black border. Hover to see the website, title, author, and duration when available.

YouTube, Vimeo, and direct videos use SpriteLink's themed playback controls. Other providers retain their embedded controls. YouTube chooses playback quality automatically; its API no longer supports requesting a target resolution. Provider restrictions, unavailable codecs, or embedding failures can be handled with **Open in Browser**. Existing GIF-style looping videos remain silent and autoplay inline.

Videos open inside the main SpriteLink window at any window size. **Pop-out** moves playback into a separate nonmodal window and removes the main-window overlay. **Mini player** keeps a compact player at the top right of the chat log. Mode changes preserve playback, and fullscreen uses the same SpriteLink controls.

DISCLAIMER: The code in this repository was created with assistance from AI tools. I made the architecture, design and implementation decisions; AI was used to write the code based on them.
