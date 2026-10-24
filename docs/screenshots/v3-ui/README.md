# AKENO 3.0 interface pass: screenshots

These images are renders of the real application window (`python -m akeno --screenshot DIR --screenshot-size WxH
--screenshot-scale F`) on Linux with Qt's offscreen platform.

What they show and don't show:
- **Font:** Inter stands in for Segoe UI, which this machine does not have.
- **Data:** the games, the hardware and the benchmarks come from `tools/fixture_home.py`. They are labelled test
  fixtures, not a real gaming PC.
- **Pixabay results:** the results in `after-find-wallpapers-fixture-*.png` are fixture data shown through the real
  page. The page says so ("TEST FIXTURE results"). No request was sent to Pixabay.
- **Windows rendering:** the Windows CI runner takes its own screenshots of the running app in every build (artifact
  `windows-test-results-and-screenshots`).

| File | Window (logical px) | Scale | Equivalent screen |
|---|---|---|---|
| `before-*-100.png` | 1920×1009 | 100 % | 1080p, before the pass |
| `after-*-100.png` | 1920×1009 | 100 % | 1080p |
| `after-library-125.png` | 1536×824 | 125 % | 1080p |
| `after-*-150.png` | 1280×680 | 150 % | 1080p |
| `after-*-200-4k.png` | 1920×1009 | 200 % | 4K |

The same window sizes are checked by `tests/test_layout_quality.py` on every page, tab and scroll position:
- no page is wider than the window;
- no spin box value is cut off;
- no label is cut off.
