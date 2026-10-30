/* Sentence splitting for speech (Blindfold mode and Reading support's read-aloud). A sentence ends
   at . ? or ! (plus any closing quote or bracket) followed by whitespace and an uppercase letter, so
   "Table 2.10" and "$1.5 million" never split, and never after a common abbreviation ("e.g. The",
   "U.S. Data", "Fig. 2", "No. 5", "vs. The") or a single initial ("J. Smith"). Short sentences are
   then joined into utterances of at most MAX characters (some browsers cut off long utterances).
   Used by the export (inlined by app/assemble.py) and the review screen; tested by
   scripts/test_sentences.js. */
(function (root) {
  "use strict";
  const MAX = 220;
  const ABBR = new Set([
    "e.g", "E.g", "i.e", "I.e", "U.S", "U.K", "U.N", "Fig", "Figs", "fig", "figs", "No", "Nos", "vs", "Vs",
    "Mr", "Mrs", "Ms", "Dr", "Prof", "St", "Jr", "Sr", "Inc", "Ltd", "Co", "Corp", "Eq", "Eqs", "eq", "eqs",
    "Vol", "vol", "pp", "approx", "Approx", "cf", "al", "Ch", "ch", "Sec", "sec",
  ]);
  const END = /[.?!]["'”’)\]]*\s+(?=["'“‘(\[]?\p{Lu})/gu;

  // The text cut into sentences: [[start offset, sentence with its trailing whitespace], ...].
  function split(text) {
    text = text || "";
    const out = [];
    let from = 0, m;
    END.lastIndex = 0;
    while ((m = END.exec(text))) {
      if (text[m.index] === ".") {
        const word = /(\S*)$/.exec(text.slice(from, m.index))[1].replace(/^["'“‘(\[]+/, "");
        if (ABBR.has(word) || /^\p{L}$/u.test(word)) continue;
      }
      const end = m.index + m[0].length;
      out.push([from, text.slice(from, end)]);
      from = end;
    }
    if (from < text.length) out.push([from, text.slice(from)]);
    return out;
  }

  // Sentences joined while they fit in max characters: [[start offset, text], ...].
  function chunks(text, max = MAX) {
    const out = [];
    for (const [start, s] of split(text)) {
      const last = out[out.length - 1];
      if (last && last[1].length + s.length <= max) last[1] += s;
      else out.push([start, s]);
    }
    return out;
  }

  const api = { split, chunks, MAX };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Sentences = api;
})(typeof window !== "undefined" ? window : globalThis);
