Test media for Desktop Studio (thumbnails, previews, wallpaper playback). Generated with ffmpeg's built-in test
patterns, no third-party footage:

    ffmpeg -f lavfi -i "testsrc2=size=320x180:rate=24:duration=3" -c:v libx264 -pix_fmt yuv420p -profile:v baseline -crf 30 -movflags +faststart sample.mp4
    ffmpeg -f lavfi -i "testsrc=size=256x144:rate=20:duration=2" -c:v libvpx-vp9 -b:v 80k sample.webm
    ffmpeg -f lavfi -i "testsrc=size=160x90:rate=10:duration=0.6" -loop 0 sample.gif
