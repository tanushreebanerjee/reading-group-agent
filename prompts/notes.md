# Your task: keep running notes of the meeting

You keep short running notes of this reading group's discussion, so that you
can follow the whole meeting and not just the last few minutes. You get your
current notes and the transcript since you last updated them (speech
recognition, so expect small errors). Return the full updated notes, at most
$max_words words in total, as bullets of at most 15 words under these headings
(leave out empty ones):

Topics: (at most 6) what has been discussed, in order.
Claims: (at most 6) claims people made about the paper, as they said them,
  with the numbers they used ("someone said level 3 works best").
Questions: (at most 5) questions people raised, each ending (answered) or (open).
Disagreements: (at most 3) where people disagreed, and the sides.
Sherlock: (at most 4) what you ($name) already said: answers and raised hands.

Rules:
- Record what was said. Do NOT judge whether claims are right or match the
  brief or paper; someone else checks that.
- Keep older items, but merge and shorten them so every section stays under
  its limit. Drop the least important when you must.
- Plain text bullets ("- "), no other formatting, no preamble.
