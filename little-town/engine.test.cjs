const assert=require('node:assert/strict');
const T=require('./engine.js');
function integrity(s){assert.equal(T.total(s),s.initialTotal);for(const a of [s.city,s.outside,s.landlord,...s.people,...s.buildings]){assert(Number.isSafeInteger(a.cash));assert(a.cash>=0)}assert(s.people.length<=90);assert(s.buildings.length<=24)}
let s=T.create();assert.equal(s.people.length,30);assert.equal(T.metrics(s).employed,26);let funds=s.city.cash;assert(T.build(s,'home',7,4));assert.equal(s.city.cash,funds-1800);integrity(s);assert(!T.build(s,'home',7,4));
for(let i=0;i<365;i++){T.tick(s);integrity(s)}console.log('baseline 365 days',T.metrics(s));
s=T.create();s.policy.wage=180;s.policy.demand=20;s.policy.rent=100;for(let i=0;i<120;i++){T.tick(s);integrity(s)}assert(s.buildings.some(b=>!b.open));const closed=s.buildings.find(b=>!b.open);assert(T.reopen(s,closed.id));assert(closed.open);integrity(s);console.log('stress / closure / reopening passed');
s=T.create();s.policy.rent=0;s.policy.wage=50;s.policy.demand=150;for(let i=0;i<365;i++){T.tick(s);integrity(s)}console.log('zero rent / high demand passed');
