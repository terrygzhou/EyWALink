import { test } from 'node:test';
import assert from 'node:assert/strict';
import { VERSION, NAME } from '../dist/index.js';

test('core package exports NAME', () => {
  assert.equal(NAME, 'EyWALink');
});

test('core package exports VERSION', () => {
  assert.equal(VERSION, '0.0.1');
});
