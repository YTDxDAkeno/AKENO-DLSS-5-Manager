"""AKENO 3.1 system tools: System Doctor, Smart Cleaner, Network Lab, Universal Mod Scanner and Security Center.

The layers are the same in every module:

* **Collection** (``providers``): what Windows, the drivers and the file system report. Each provider has a
  Windows implementation and an in-memory stand-in with the same calls, so every analysis runs in the test suite.
  A value Windows does not report stays ``None`` with the reason; nothing here guesses a sensor value.
* **Diagnosis** (``model.Finding``): the shared result type. A finding separates what was *measured*, what is a
  *possible cause* and what is only a *correlation in time*, and names the next step.
* **Actions**: only what the user confirmed runs, it is written to the local journal (``store``) before it runs,
  and everything that can be undone records how.
* **Tasks** (``tasks.TaskContext``): every long operation can be cancelled, has a time limit and reports progress
  only when it knows it.

No Qt below ``akeno/ui``. Nothing in this package sends data anywhere unless the user starts a network test, and
results stay in ``%LOCALAPPDATA%\\AKENO\\system.sqlite3``.
"""
