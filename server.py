import os
import json
import re
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import anthropic

# Load .env file if present (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder='public', static_url_path='')
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

SLIDE_PLANS = {
    4:  ['hook', 'pain', 'value', 'cta'],
    5:  ['hook', 'pain', 'relate', 'value', 'cta'],
    6:  ['hook', 'pain', 'relate', 'shift', 'value', 'cta'],
    7:  ['hook', 'pain', 'relate', 'shift', 'value', 'proof', 'cta'],
    8:  ['hook', 'pain', 'relate', 'shift', 'value', 'value', 'proof', 'cta'],
    9:  ['hook', 'pain', 'relate', 'relate', 'shift', 'value', 'value', 'proof', 'cta'],
    10: ['hook', 'pain', 'relate', 'relate', 'shift', 'value', 'value', 'proof', 'proof', 'cta'],
}

SYSTEM_PROMPT = """You are an expert Instagram story scriptwriter for Creative Directors. You write punchy, emotionally resonant, scroll-stopping copy.

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
- Return ONLY valid JSON, no markdown, no explanation"""


def get_slide_plan(length):
    length = max(4, min(10, int(length)))
    return SLIDE_PLANS.get(length, SLIDE_PLANS[7])


@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


@app.route('/api/inspire', methods=['POST'])
def inspire():
    data = request.get_json()
    topic = (data or {}).get('topic', '').strip()
    if not topic:
        return jsonify({'error': 'Topic is required'}), 400

    try:
        response = client.messages.create(
            model='claude-opus-4-6',
            max_tokens=2000,
            system='You are a Creative Director story angle generator. Return ONLY valid JSON arrays, no markdown fences, no explanation.',
            messages=[{
                'role': 'user',
                'content': f'''Generate 6 distinct, compelling Instagram story angles for a Creative Director on the topic: "{topic}"

Each angle should take a different perspective (e.g., personal story, industry myth, hot take, behind-the-scenes, framework, case study).

Return as a JSON array:
[
  {{
    "title": "Angle title (4-6 words)",
    "hook": "The exact opening line that would stop the scroll (max 12 words)",
    "angle": "One sentence describing the unique perspective this story takes"
  }}
]'''
            }]
        )

        text = response.content[0].text.strip()
        json_match = re.search(r'\[[\s\S]*\]', text)
        if not json_match:
            raise ValueError('Could not parse ideas from response')

        ideas = json.loads(json_match.group(0))
        return jsonify({'ideas': ideas})

    except Exception as e:
        print(f'Inspire error: {e}')
        return jsonify({'error': str(e) or 'Failed to generate ideas'}), 500


@app.route('/api/generate', methods=['POST'])
def generate():
    data = request.get_json()
    mode = (data or {}).get('mode', 'inspire')
    input_text = (data or {}).get('input', '').strip()
    length = (data or {}).get('length', 7)

    if not input_text:
        return jsonify({'error': 'Input is required'}), 400

    slide_plan = get_slide_plan(length)
    slide_types = ' → '.join(slide_plan)

    if mode == 'braindump':
        user_prompt = f'''I've written these raw, unpolished thoughts. Transform them into a polished {length}-slide Instagram story script that stays true to my voice and ideas:

---
{input_text}
---

Use exactly these slide types in this order: {slide_types}

Return a JSON array with exactly {len(slide_plan)} slides:
[
  {{
    "type": "hook",
    "headline": "Short punchy headline (max 8 words)",
    "body": "2-3 sentences of engaging, conversational copy",
    "direction": "Brief creative direction note (visual/tone/mood)"
  }}
]'''
    else:
        user_prompt = f'''Create a {length}-slide Instagram story script for a Creative Director about: "{input_text}"

Use exactly these slide types in this order: {slide_types}

Return a JSON array with exactly {len(slide_plan)} slides:
[
  {{
    "type": "hook",
    "headline": "Short punchy headline (max 8 words)",
    "body": "2-3 sentences of engaging, conversational copy",
    "direction": "Brief creative direction note (visual/tone/mood)"
  }}
]'''

    def generate_stream():
        full_text = ''
        try:
            yield f"data: {json.dumps({'type': 'start', 'total': len(slide_plan)})}\n\n"

            with client.messages.stream(
                model='claude-opus-4-6',
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': user_prompt}]
            ) as stream:
                for event in stream:
                    if (event.type == 'content_block_delta' and
                            event.delta.type == 'text_delta'):
                        full_text += event.delta.text
                        yield f"data: {json.dumps({'type': 'chunk', 'text': event.delta.text})}\n\n"

            json_match = re.search(r'\[[\s\S]*\]', full_text)
            if json_match:
                slides = json.loads(json_match.group(0))
                yield f"data: {json.dumps({'type': 'complete', 'slides': slides})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Failed to parse story — please try again'})}\n\n"

        except Exception as e:
            print(f'Generate error: {e}')
            yield f"data: {json.dumps({'type': 'error', 'message': str(e) or 'Generation failed'})}\n\n"

        yield 'data: [DONE]\n\n'

    return Response(
        stream_with_context(generate_stream()),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    print(f'✦ Story Engine running → http://localhost:{port}')
    app.run(host='0.0.0.0', port=port, debug=False)
