#!/usr/bin/env node
'use strict';

// Generates synthetic contract documents (as an NDJSON _bulk payload) for
// either the contracts-v2.37 or contracts-v2.40 index. Unlike a hand-picked
// field subset, this walks the *actual* mapping JSON in ./schemas/<index>.json
// and produces a value for every declared field, so the sample data covers
// the full spec rather than a curated slice of it.
//
// Usage: node generate-contract-sample-data.js <index> <count>

const fs = require('fs');
const path = require('path');

const index = process.argv[2];
const count = parseInt(process.argv[3] || '500', 10);

if (!index || Number.isNaN(count)) {
  console.error('Usage: node generate-contract-sample-data.js <index> <count>');
  process.exit(1);
}

const mappingPath = path.join(__dirname, 'schemas', `${index}.json`);
if (!fs.existsSync(mappingPath)) {
  console.error(`Mapping file not found: ${mappingPath}`);
  process.exit(1);
}

const mapping = JSON.parse(fs.readFileSync(mappingPath, 'utf8'));
const rootProperties = mapping.mappings.properties;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function pad(n) {
  return String(n).padStart(2, '0');
}

function randomDateISO(startYear, endYear) {
  const y = randInt(startYear, endYear);
  const m = randInt(1, 12);
  const d = randInt(1, 28);
  const hh = randInt(0, 23);
  const mm = randInt(0, 59);
  const ss = randInt(0, 59);
  return `${y}-${pad(m)}-${pad(d)}T${pad(hh)}:${pad(mm)}:${pad(ss)}Z`;
}

function randomDateOnly(startYear, endYear) {
  const y = randInt(startYear, endYear);
  const m = randInt(1, 12);
  const d = randInt(1, 28);
  return `${y}-${pad(m)}-${pad(d)}`;
}

function uuidish(prefix) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}-${Math.random().toString(36).slice(2, 10)}`;
}

function titleCase(s) {
  return s.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, (c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// Word banks for name-based heuristics (kept generic on purpose — the goal
// is full field coverage with plausible-looking values, not perfect realism)
// ---------------------------------------------------------------------------

const FIRST_NAMES_EN = ['Ahmed', 'Mohammed', 'Ali', 'Omar', 'Khalid', 'Fatima', 'Aisha', 'Sara', 'Layla', 'Noor', 'John', 'Maria', 'James', 'Emma', 'David'];
const LAST_NAMES_EN = ['Al Maktoum', 'Al Nahyan', 'Hassan', 'Khan', 'Smith', 'Johnson', 'Al Farsi', 'Al Suwaidi', 'Brown', 'Wilson'];
const GENERIC_AR_WORDS = ['الفطيم لتأجير السيارات', 'دايموند ليس', 'فاست رنت', 'سكاي موتورز', 'محمد', 'فاطمة', 'دبي', 'أبوظبي', 'الشارقة'];
const GENERIC_EN_WORDS = ['Al Futtaim Rent a Car', 'Diamond Lease', 'Fast Rent', 'Sky Motors', 'National Car Rental', 'Thrifty UAE', 'Yelo Rent a Car', 'Dubai', 'Abu Dhabi', 'Sharjah'];
const NATIONALITIES = ['UAE', 'IND', 'PAK', 'GBR', 'USA', 'EGY', 'PHL', 'JOR', 'SYR', 'LBN'];
const SOURCES = ['odoo', 'salesforce', 'sap', 'legacy-crm', 'manual'];
const STATUSES = ['active', 'pending', 'completed', 'cancelled', 'expired'];
const KINDS = ['rental', 'lease', 'corporate', 'individual'];
const CITIES = ['Dubai', 'Abu Dhabi', 'Sharjah', 'Al Ain'];
const CAR_MAKES = ['Toyota', 'Nissan', 'Honda', 'Mercedes', 'BMW', 'Chevrolet', 'Ford', 'Kia'];
const CURRENCIES = ['AED', 'USD', 'EUR'];

// ---------------------------------------------------------------------------
// Field-name-driven scalar generator
// ---------------------------------------------------------------------------

function generateStringValue(fieldName) {
  const f = fieldName.toLowerCase();

  if (f === 'did' || f.endsWith('did')) return uuidish('id');
  if (f.includes('email')) return `${pick(['ahmed', 'sara', 'mohammed', 'layla', 'john', 'maria'])}.${randInt(1, 999)}@example.com`;
  if (f === 'phone' || f.includes('phonenumber')) return `+9715${randInt(10000000, 99999999)}`;
  if (f === 'countrycode') return pick(['+971', '+91', '+92', '+44', '+1']);
  if (f.includes('imageurl') || f.includes('url') || f === 'file' || f === 'slug') return `https://cdn.example.com/${uuidish('asset')}.jpg`;
  if (f === 'firstname') return pick(FIRST_NAMES_EN);
  if (f === 'lastname' || f === 'middlename') return pick(LAST_NAMES_EN);
  if (f === 'fullname') return `${pick(FIRST_NAMES_EN)} ${pick(LAST_NAMES_EN)}`;
  if (f === 'ar') return pick(GENERIC_AR_WORDS);
  if (f === 'en') return pick(GENERIC_EN_WORDS);
  if (f === 'gender') return pick(['male', 'female']);
  if (f.includes('nationality')) return pick(NATIONALITIES);
  if (f === 'status') return pick(STATUSES);
  if (f === 'kind') return pick(KINDS);
  if (f === 'customertype') return pick(['individual', 'corporate']);
  if (f === 'currencycode') return pick(CURRENCIES);
  if (f === 'vin') return Math.random().toString(36).slice(2, 19).toUpperCase();
  if (f === 'number' && (f.includes('plate') || f.includes('makani'))) return `${randInt(1, 99999)}`;
  if (f === 'code') return `${pick(['A', 'B', 'C', 'D'])}${randInt(1, 99)}`;
  if (f === 'source') return pick(SOURCES);
  if (f === 'sourcekey') return `KEY-${randInt(1000, 9999)}`;
  if (f.includes('licenseno') || f.includes('licensenumber')) return `TL-${randInt(10000, 99999)}`;
  if (f === 'makani') return String(randInt(10000000, 99999999));
  if (f === 'location' && !f.includes('type')) return pick(CITIES);
  if (f.includes('city')) return pick(CITIES);
  if (f === 'title') return pick(['Mr', 'Mrs', 'Dr', 'Eng']);
  if (f === 'suffix') return pick(['Jr', 'Sr', '']);
  if (f.includes('vehiclemake') || (f === 'name' && false)) return pick(CAR_MAKES);

  return `${titleCase(fieldName)}-${randInt(1, 9999)}`;
}

function generateDateValue(def) {
  if (def.format) {
    // Custom formats declared on driverLicense.expiryDate etc. always list
    // a plain yyyy-MM-dd alternative first — use that for safety.
    return randomDateOnly(2018, 2027);
  }
  return randomDateISO(2019, 2026);
}

function generateNumberValue(type) {
  switch (type) {
    case 'long':
    case 'integer':
    case 'short':
    case 'byte':
      return randInt(1, 999999);
    case 'float':
    case 'double':
      return Math.round(Math.random() * 10000000) / 100;
    default:
      return randInt(1, 999999);
  }
}

// ---------------------------------------------------------------------------
// Mapping-driven recursive document builder
// ---------------------------------------------------------------------------

function generateValue(fieldName, def) {
  if (def.properties) {
    if (def.type === 'nested') {
      // Keep nested arrays at length 1 to avoid combinatorial size blow-up
      // across deeply nested (details -> drivers -> contact -> addresses)
      // chains while still covering every field once.
      return [generateObject(def.properties)];
    }
    return generateObject(def.properties);
  }

  switch (def.type) {
    case 'date':
      return generateDateValue(def);
    case 'long':
    case 'integer':
    case 'short':
    case 'byte':
    case 'float':
    case 'double':
      return generateNumberValue(def.type);
    case 'boolean':
      return Math.random() > 0.5;
    case 'geo_point':
      return { lat: Number((24 + Math.random()).toFixed(6)), lon: Number((54 + Math.random()).toFixed(6)) };
    case 'object':
      // Dynamic (no explicit sub-properties) or disabled (events) objects —
      // keep empty so dynamic mapping never sees conflicting shapes/types
      // across the 500 generated documents.
      return {};
    case 'text':
    case 'keyword':
    default:
      return generateStringValue(fieldName);
  }
}

function generateObject(properties) {
  const obj = {};
  for (const [key, def] of Object.entries(properties)) {
    obj[key] = generateValue(key, def);
  }
  return obj;
}

// ---------------------------------------------------------------------------
// Emit NDJSON bulk payload
// ---------------------------------------------------------------------------

const lines = [];
for (let i = 0; i < count; i++) {
  const doc = generateObject(rootProperties);
  lines.push(JSON.stringify({ index: { _index: index, _id: `${index}-sample-${i + 1}` } }));
  lines.push(JSON.stringify(doc));
}
process.stdout.write(lines.join('\n') + '\n');
