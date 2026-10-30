// Sentence splitting for speech (web/sentences.js). Run: node scripts/test_sentences.js
"use strict";
const assert = require("node:assert/strict");
const { split, chunks } = require("../web/sentences.js");

const sentences = (t) => split(t).map(([, s]) => s.trim());
const cases = [
  // [text, expected sentences]
  ["Table 2.10 shows the three age groups.", ["Table 2.10 shows the three age groups."]],
  ["Retirees are 38%. Table 2.10 shows the three age groups.",
   ["Retirees are 38%.", "Table 2.10 shows the three age groups."]],
  ["Use more data, e.g. the U.S. data from 2020.", ["Use more data, e.g. the U.S. data from 2020."]],
  ["See e.g. The Times. It agrees.", ["See e.g. The Times.", "It agrees."]],
  ["Growth in the U.S. Data from Canada differ.", ["Growth in the U.S. Data from Canada differ."]],
  ["As in Fig. 2 and Fig. Three.", ["As in Fig. 2 and Fig. Three."]],
  ["Item No. 5 sold. No. Twelve did not.", ["Item No. 5 sold.", "No. Twelve did not."]],
  ["Cats vs. Dogs is a study. It is old.", ["Cats vs. Dogs is a study.", "It is old."]],
  ["That is, i.e. The point. Next one.", ["That is, i.e. The point.", "Next one."]],
  ["Written by J. Smith in 2001. Read it.", ["Written by J. Smith in 2001.", "Read it."]],
  ["The mean is 3.5. The median is 4.", ["The mean is 3.5.", "The median is 4."]],
  ["It costs $1.5 million. Is it worth it? Yes! Buy it.", ["It costs $1.5 million.", "Is it worth it?", "Yes!", "Buy it."]],
  ["He said \"stop.\" Then he left.", ["He said \"stop.\"", "Then he left."]],
  ["(See Table 2.9.) The bars fall.", ["(See Table 2.9.)", "The bars fall."]],
  ["Values rise. then fall.", ["Values rise. then fall."]],               // lowercase after: not a split
  ["The ratio is 2.5:1; it holds. Done.", ["The ratio is 2.5:1; it holds.", "Done."]],  // no split at ; or :
  ["", []],
];
let failed = 0;
for (const [text, want] of cases) {
  try {
    assert.deepEqual(sentences(text), want);
  } catch (e) {
    failed++;
    console.log(`FAIL ${JSON.stringify(text)}\n  got  ${JSON.stringify(sentences(text))}\n  want ${JSON.stringify(want)}`);
  }
}

// Offsets point into the original text (read-aloud highlights words from them).
const text = "Retirees are 38%. Table 2.10 shows the groups.";
for (const [start, s] of split(text)) assert.equal(text.slice(start, start + s.length), s);

// Joining: the Park City paragraph from PDF page 85 stays whole at "Table 2.10".
const park = "The population in Park City is made up of children, working-age adults, and retirees. Table 2.10 shows "
  + "the three age groups, the number of people in the town from each age group, and the proportion (%) of people in "
  + "each age group. Construct a bar graph showing the proportions.";
const parts = chunks(park);
assert.ok(parts.every(([, c]) => c.length <= 220), "a chunk is over 220 characters");
assert.ok(parts.every(([, c]) => !/\b2\.\s*$/.test(c)), `split inside "2.10": ${JSON.stringify(parts)}`);
assert.equal(parts.map(([, c]) => c).join(""), park);
assert.deepEqual(parts.map(([, c]) => c.trim()), [
  "The population in Park City is made up of children, working-age adults, and retirees.",
  "Table 2.10 shows the three age groups, the number of people in the town from each age group, and the proportion (%) of people in each age group. Construct a bar graph showing the proportions.",
]);

console.log(failed ? `${failed} of ${cases.length} cases failed` : `all ${cases.length} cases and the joining checks passed`);
process.exit(failed ? 1 : 0);
