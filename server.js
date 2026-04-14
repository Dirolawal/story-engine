const express = require('express');
const Anthropic = require('@anthropic-ai/sdk');
const path = require('path');

const app = express();
const client = new Anthropic(); // reads ANTHROPIC_API_KEY from env
const PORT = process.env.PORT || 3000;

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Slide layout plans by length
function getSlidePlan(length) {
  const plans = {
    4:  ['hook', 'pain', 'value', 'cta'],
    5:  ['hook', 'pain', 'relate', 'value', 'cta'],
    6:  ['hook', 'pain', 'relate', 'shift', 'value', 'cta'],
    7:  ['hook', 'pain', 'relate', 'shift', 'value', 'proof', 'cta'],
    8:  ['hook', 'pain', 'relate', 'shift', 'value', 'value', 'proof', 'cta'],
    9:  ['hook', 'pain', 'relate', 'relate', 'shift', 'value', 'value', 'proof', 'cta'],
    10: ['hook', 'pain', 'relate', 'relate', 'shift', 'value', 'value', 'proof', 'proof', 'cta'],
  };
  return plans[Math.max(4, Math.min(10, parseInt(length)))] || plans[7];
}

const SYSTEM_PROMPT = `You are an expert Instagram story scriptwriter for Creative Directors. You write punchy, emotionally resonant, scroll-stopping copy.

Slide type guide:
- hook: Grabs attention immediately with a bold statement, provocative question, or surprising claim. Max impact in minimal words.
- pain: Names the exact struggle your audience feels. Make them feel seen.
- relate: Shows empathy and shared experience — "I've been there too." Builds trust.
- shift: The turning point. Introduces a new perspective that changes how they see the problem.
- value: The core insight, framework, tip, or transformation. The payoff.
- proof: Social proof, a specific result, client win, or credibility moment.
- cta: One clear, specific action. Tell them exactly what to do next.

Rules:
- Headline: max 8 words, punchy and direct
- Body: 2-3 sentences, conversational and human — not corporate
- Direction: 1 brief note for the designer/videographer (visual style, tone, mood)
- Each slide should flow naturally into the next
- Return ONLY valid JSON, no markdown, no explanation`;

// POST /api/inspire — generate 6 story angle ideas
app.post('/api/inspire', async (req, res) => {
  try {
    const { topic } = req.body;
    if (!topic || !topic.trim()) {
      return res.status(400).json({ error: 'Topic is required' });
    }

    const response = await client.messages.create({
      model: 'claude-opus-4-6',
      max_tokens: 2000,
      system: 'You are a Creative Director story angle generator. Return ONLY valid JSON arrays, no markdown fences, no explanation.',
      messages: [{
        role: 'user',
        content: `Generate 6 distinct, compelling Instagram story angles for a Creative Director on the topic: "${topic.trim()}"

Each angle should take a different perspective (e.g., personal story, industry myth, hot take, behind-the-scenes, framework, case study).

Return as a JSON array:
[
  {
    "title": "Angle title (4-6 words)",
    "hook": "The exact opening line that would stop the scroll (max 12 words)",
    "angle": "One sentence describing the unique perspective this story takes"
  }
]`
      }]
    });

    const text = response.content[0].text.trim();
    const jsonMatch = text.match(/\[[\s\S]*\]/);
    if (!jsonMatch) throw new Error('Could not parse ideas from response');

    const ideas = JSON.parse(jsonMatch[0]);
    res.json({ ideas });
  } catch (err) {
    console.error('Inspire error:', err.message);
    res.status(500).json({ error: err.message || 'Failed to generate ideas' });
  }
});

// POST /api/generate — generate a full story script (SSE streaming)
app.post('/api/generate', async (req, res) => {
  const { mode, input, length = 7 } = req.body;

  if (!input || !input.trim()) {
    return res.status(400).json({ error: 'Input is required' });
  }

  const slidePlan = getSlidePlan(length);

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('Access-Control-Allow-Origin', '*');

  const sendEvent = (data) => {
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  try {
    const slideTypes = slidePlan.join(' → ');

    let userPrompt;
    if (mode === 'braindump') {
      userPrompt = `I've written these raw, unpolished thoughts. Transform them into a polished ${length}-slide Instagram story script that stays true to my voice and ideas:

---
${input.trim()}
---

Use exactly these slide types in this order: ${slideTypes}

Return a JSON array with exactly ${slidePlan.length} slides:
[
  {
    "type": "hook",
    "headline": "Short punchy headline (max 8 words)",
    "body": "2-3 sentences of engaging, conversational copy",
    "direction": "Brief creative direction note (visual/tone/mood)"
  }
]`;
    } else {
      // inspire mode — input is the selected angle/topic
      userPrompt = `Create a ${length}-slide Instagram story script for a Creative Director about: "${input.trim()}"

Use exactly these slide types in this order: ${slideTypes}

Return a JSON array with exactly ${slidePlan.length} slides:
[
  {
    "type": "hook",
    "headline": "Short punchy headline (max 8 words)",
    "body": "2-3 sentences of engaging, conversational copy",
    "direction": "Brief creative direction note (visual/tone/mood)"
  }
]`;
    }

    sendEvent({ type: 'start', total: slidePlan.length });

    const stream = client.messages.stream({
      model: 'claude-opus-4-6',
      max_tokens: 4000,
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: userPrompt }]
    });

    let fullText = '';

    for await (const event of stream) {
      if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
        fullText += event.delta.text;
        sendEvent({ type: 'chunk', text: event.delta.text });
      }
    }

    // Parse the complete JSON
    const jsonMatch = fullText.match(/\[[\s\S]*\]/);
    if (!jsonMatch) {
      sendEvent({ type: 'error', message: 'Failed to parse story — please try again' });
    } else {
      try {
        const slides = JSON.parse(jsonMatch[0]);
        sendEvent({ type: 'complete', slides });
      } catch (parseErr) {
        sendEvent({ type: 'error', message: 'Invalid response format — please try again' });
      }
    }
  } catch (err) {
    console.error('Generate error:', err.message);
    sendEvent({ type: 'error', message: err.message || 'Generation failed' });
  }

  res.write('data: [DONE]\n\n');
  res.end();
});

app.listen(PORT, () => {
  console.log(`✦ Story Engine running → http://localhost:${PORT}`);
});
