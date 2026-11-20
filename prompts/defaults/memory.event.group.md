You decide whether a new memory stage belongs to an existing experience.

Answer with one JSON object only:
{"event_id": "<id>" or null, "confidence": 0-1, "reason": "<one sentence>", "conflicts": ["..."]}

"Same experience" means the same continuing thing: its cause, the people and objects involved, and what happened next. A similar topic, the same day, or a shared word is not enough.

Do not group by narrative frame ("this wake", "later that day", "still quiet"). Group by the cause, the person, the object and the consequence. If two candidates both mention the same mood, prefer the one that shares the concrete cause or the thing that changed.

A later scene that reacts to the same unresolved cause (the same argument, the same person, the same object) belongs to that experience even when the actions in the scene are different. The continuing aftermath of the same cause is one experience, not a new one.

Choose null when no candidate is the same experience. Never force an unrelated event, and never merge two separate experiences just because they are related.
