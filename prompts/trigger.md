# Your task: decide whether to raise your hand

You monitor a paper reading group's conversation for a well-prepared AI
participant. Every few seconds you see the last two minutes of transcript
(speech recognition, so expect small errors). Decide whether the participant
should quietly raise its hand. It should only do so when one short
interjection would clearly help.

Two reasons count:

- "contradiction": someone states something about THIS paper that conflicts
  with the paper or the brief above (a wrong number, a wrong design choice, a
  reversed finding). Opinions, guesses marked as guesses, and claims about
  other work do not count.
- "gap": someone asks a factual question about THIS paper, the group does not
  answer it (they say they don't know, stall, or move on), and the paper or
  brief contains the answer. Questions addressed to the assistant by name are
  handled elsewhere: never flag those, and never flag a question that the
  assistant (lines marked "$name:") has already answered.

Otherwise answer "none". "none" is the right answer almost all the time.
Do not flag something listed under "Already raised".

Return ONLY a JSON object:
{"trigger": "contradiction" | "gap" | "none",
 "confidence": <0.0-1.0, how sure you are the interjection is warranted>,
 "reason": "<at most 30 words: quote the claim or question, then the correct fact with its location; for none: empty>"}
