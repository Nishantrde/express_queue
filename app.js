const express = require('express');
const axios = require('axios');
const FormData = require('form-data');
const fs = require('fs');
const path = require('path');
const os = require("os")
const cors = require("cors");
const { CLIENT_RENEG_LIMIT } = require('tls');

const app = express();
const PORT = 3000;

app.use(express.json());
app.use(cors())

class Queue {
  constructor() {
    this.tasks = [];
    this.running = false;
    this.current = null;      // currently running task object
    this.finished = new Set();// names of finished tasks
  }

  enqueue(task) {
    console.log(`[QUEUE] Enqueuing new task: ${task.name}`);
    this.tasks.push(task);
    this.runNext();
  }

  async runNext() {
    if (this.running) return;
    this.current = this.tasks.shift();
    if (!this.current) {
      console.log(`[QUEUE] No tasks left to run.`);
      return;
    }

    this.running = true;
    console.log(`[QUEUE] Running task: ${next.name}`);

    try {
      await next.run();
      console.log(`[QUEUE] Task completed: ${next.name}`);
    } catch (err) {
      console.error(`[QUEUE] Error in task ${next.name}:`, err);
      if (next.onError) {
        try { next.onError(err); } catch (e) { }
      }
    } finally {
      if (this.current && this.current.name) {
        this.finished.add(this.current.name);
        console.log(`[QUEUE] Marked as finished: ${this.current.name}`);
      }
      this.current = null;
      this.running = false;
      setImmediate(() => this._runNext());
    }
  }

  // ---- Restored helper methods ----

  moveToFront(index) {
    console.log(`[QUEUE] Requested moveToFront for index ${index}`);
    if (index < 0 || index >= this.tasks.length) {
      console.error("[QUEUE] Invalid index for moveToFront");
      return;
    }
    const [item] = this.tasks.splice(index, 1);
    this.tasks.unshift(item);
    console.log(`[QUEUE] Moved "${item.name}" to the front of the queue`);
  }

  size() {
    return this.tasks.length;
  }

  getPosition(name) {
    if (this.current && this.current.name === name) return 0; // running
    for (let i = 0; i < this.tasks.length; i++) {
      if (this.tasks[i].name === name) return i + 1; // queued
    }
    if (this.finished.has(name)) return -1; // finished
    return -1; // not found
  }
}

const jQ = new Queue();
let taskCounter = 1;

function parseFlaskJsonOrText(resData) {
  if (resData && typeof resData === 'object') return { ok: true, data: resData };
  try {
    const parsed = JSON.parse(resData);
    return { ok: true, data: parsed };
  } catch (e) {
    return { ok: false, text: String(resData).slice(0, 200) };
  }
}

app.post('/create', async (req, res) => {
  const taskName = `FaceSearchTask-${taskCounter++}`;
  let img = req.body.img.replace(/^data:image\/jpeg;base64,/, "")

  const task = {
    name: taskName,
    run: async () => {
      console.log(`[TASK:${taskName}] Starting execution...`);
      res.set({
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive'
      });
      res.flushHeaders?.();
      if (!img) {
        res.write(`data: ${JSON.stringify({ type: 'error', message: 'Invalid base64 data URI' })}\n\n`);
        return;
      }

      // Create FormData and append as a file-like buffer
      const form = new FormData();
      form.append('selfie', img, {
        filename,
        contentType: "jpeg"
      });
      form.append('top_k', '10')

      const flaskUrl = 'http://127.0.0.1:5000/api/search';
      console.log(`[TASK:${taskName}] Uploading selfie to Flask service...`);
      res.write(`data: Uploading selfie to face-search service...\n\n`);

      const headers = form.getHeaders();
      let uploadResponse;
      try {
        uploadResponse = await axios.post(flaskUrl, form, {
          headers,
          responseType: 'text',
          maxContentLength: Infinity,
          maxBodyLength: Infinity,
          timeout: 120000
        });
        console.log(`[TASK:${taskName}] Flask upload complete.`);
      } catch (err) {
        console.error(`[TASK:${taskName}] Upload failed:`, err.message);
        res.write(`data: ${JSON.stringify({ type: 'error', message: 'Upload failed', detail: String(err) })}\n\n`);
        return;
      }

      // console.log(`[TASK:${taskName}] Parsing Flask response...`);
      res.write(`data: Received response from face-search service. Parsing results...\n\n`);
      const parsed = parseFlaskJsonOrText(uploadResponse.data);

      if (!parsed.ok) {
        console.error(`[TASK:${taskName}] Non-JSON response returned from Flask`);
        res.write(`data: ${JSON.stringify({ type: 'error', message: 'Face-search returned non-JSON response', snippet: parsed.text })}\n\n`);
        return;
      }

      const data = parsed.data;
      const matches = Array.isArray(data.matches) ? data.matches : [];

      if (matches.length === 0) {
        console.log(`[TASK:${taskName}] No matches found.`);
        res.write(`data: No matches found.\n\n`);
      } else {
        console.log(`[TASK:${taskName}] Found ${matches.length} matches.`);
        res.write(`data: Found ${matches.length} matches. Sending back images as data URIs...\n\n`);
      }

      for (let i = 0; i < matches.length; i++) {
        const m = matches[i];
        console.log(`[TASK:${taskName}] Sending match #${i + 1}`);
        const payload = { index: i + 1, data_uri: m.data_uri, score: m.score, original: m.original };
        res.write(`data: ${JSON.stringify({ type: 'match', payload })}\n\n`);
      }

      console.log(`[TASK:${taskName}] Task done.`);
      res.write(`data: ${JSON.stringify({ type: 'done', payload: { count: matches.length } })}\n\n`);
    },
    onError: (err) => {
      console.error(`[TASK:${taskName}] Exception during execution:`, err);
      try {
        res.write(`data: ${JSON.stringify({ type: 'error', message: 'Task exception', detail: String(err) })}\n\n`);
      } catch (e) { }
    }
  };

  jQ.enqueue(task);
  res.write(`data: Task "${task.name}" added to queue.\n\n`);

  const interval = setInterval(() => {
    const pos = jQ.getPosition(task.name);
    // console.log(`[QUEUE] Task ${task.name} position: ${pos}`);
    res.write(`data: ${JSON.stringify({ type: 'position', payload: { name: task.name, position: pos } })}\n\n`);
    if (pos === -1) {
      console.log(`[QUEUE] Task ${task.name} finished. Closing SSE.`);
      clearInterval(interval);
      setTimeout(() => {
        try { res.end(); } catch (e) { }
      }, 400);
    }
  }, 1000);

  req.on('close', () => {
    console.log(`[ROUTE] Client closed connection for ${task.name}`);
    clearInterval(interval);
  });
});

// ---------- restored endpoints ----------

// Move a queued task to the front
app.post('/addToFront', (req, res) => {
  console.log(`[ROUTE] /addToFront called with body:`, req.body);
  const { index } = req.body;
  if (index === undefined) {
    return res.status(400).send("Please provide index");
  }

  try {
    jQ.moveToFront(index);
    res.send({ message: `Moved task at index ${index} to front.` });
  } catch (err) {
    res.status(400).send(err.message);
  }
});

app.post('/pause', (req, res) => {
  jQ.running = req.body;
  res.send({ "status": "ok", "running": jQ.running })
});

app.listen(PORT, () => {
  const networkInterfaces = os.networkInterfaces();
  let localIps = [];

  for (const interfaceName in networkInterfaces) {
    for (const iface of networkInterfaces[interfaceName]) {
      if (!iface.internal && iface.family === 'IPv4') {
        localIps.push(iface.address);
      }
    }
  }

  if (localIps.length > 0) {
    console.log(`Local IP addresses: ${localIps.join(', ')}:${PORT}`);
  } else {
    console.log('No local IPv4 address found.');
  }

  console.log(`Server is listening on port ${PORT}`);
});
