#!/usr/bin/env node
import { pocketParity } from '../src/cli.mjs';

try {
  process.exitCode = await pocketParity(process.argv.slice(2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 2;
}
