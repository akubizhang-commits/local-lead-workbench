import test from 'node:test';
import assert from 'node:assert/strict';
import {cleanAccountTitle} from '../src/socialDisplay.ts';
test('generic platform titles are removed but real account names remain',()=>{
 assert.equal(cleanAccountTitle('(4) Facebook'),'');
 assert.equal(cleanAccountTitle('Mure + Grand (@mureandgrand) · Instagram 照片和视频'),'Mure + Grand');
 assert.equal(cleanAccountTitle('Mure + Grand ™'),'Mure + Grand ™');
});
