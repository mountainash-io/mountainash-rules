//! Canonical symbolic DFA operations; no parser or Python runtime dependency.

use super::{
    Budget, LanguageError, Limits, Rows, Transition, MAX_SCALAR, SURROGATE_HI, SURROGATE_LO,
};
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;

#[derive(Clone, Debug)]
pub struct Dfa {
    transitions: Arc<Rows>,
    accepting: Vec<bool>,
    edge_count: usize,
}

fn invalid(message: impl Into<String>) -> LanguageError {
    LanguageError::InvalidWire(message.into())
}

fn append(row: &mut Vec<Transition>, edge: Transition) {
    if let Some(previous) = row.last_mut() {
        if previous.1 + 1 == edge.0 && previous.2 == edge.2 {
            previous.1 = edge.1;
            return;
        }
    }
    row.push(edge);
}

fn total_edges(rows: &Rows, budget: &mut Budget) -> Result<usize, LanguageError> {
    budget.states(rows.len())?;
    budget.spend(rows.len())?;
    let mut total = 0usize;
    for row in rows {
        total = total
            .checked_add(row.len())
            .ok_or(LanguageError::Resource {
                resource: "transitions",
                limit: budget.limits.max_transitions,
            })?;
        budget.transitions(total)?;
    }
    Ok(total)
}

/// Refine symbolic transition functions directly, avoiding a dense global alphabet.
fn partition(
    rows: &Rows,
    accepting: &[bool],
    budget: &mut Budget,
) -> Result<(Vec<usize>, usize), LanguageError> {
    let n = rows.len();
    budget.spend(n)?;
    let mut blocks: Vec<usize> = accepting.iter().map(|&value| usize::from(value)).collect();
    let mut previous_count = usize::MAX;
    loop {
        budget.spend(n)?;
        let mut next = Vec::with_capacity(n);
        let mut signatures: HashMap<(usize, Vec<Transition>), usize> = HashMap::new();
        for (state, row) in rows.iter().enumerate() {
            budget.spend(row.len())?;
            let mut signature = Vec::with_capacity(row.len());
            for &(lo, hi, target) in row {
                append(&mut signature, (lo, hi, blocks[target]));
            }
            let new_id = signatures.len();
            next.push(
                *signatures
                    .entry((blocks[state], signature))
                    .or_insert(new_id),
            );
        }
        let count = signatures.len();
        blocks = next;
        if count == previous_count {
            return Ok((blocks, count));
        }
        previous_count = count;
    }
}

pub(crate) fn minimize(
    rows: Rows,
    accepting: Vec<bool>,
    start: usize,
    budget: &mut Budget,
) -> Result<Dfa, LanguageError> {
    let n = rows.len();
    if n == 0 || accepting.len() != n || start >= n {
        return Err(invalid("invalid raw automaton state vectors"));
    }
    let edges = total_edges(&rows, budget)?;
    budget.spend(edges)?;
    if rows.iter().flatten().any(|&(_, _, target)| target >= n) {
        return Err(invalid("transition target outside the automaton"));
    }
    let (blocks, count) = partition(&rows, &accepting, budget)?;
    budget.spend(n)?;
    budget.spend(count)?;
    let mut representative = vec![usize::MAX; count];
    for (state, &block) in blocks.iter().enumerate() {
        if representative[block] == usize::MAX {
            representative[block] = state;
        }
    }
    let mut new_ids = vec![usize::MAX; count];
    let mut order = vec![blocks[start]];
    new_ids[blocks[start]] = 0;
    let mut output = Vec::new();
    let mut flags = Vec::new();
    let mut edge_count = 0usize;
    let mut cursor = 0;
    while cursor < order.len() {
        budget.spend(1)?;
        let source = representative[order[cursor]];
        budget.spend(rows[source].len())?;
        let mut row = Vec::with_capacity(rows[source].len());
        for &(lo, hi, target) in &rows[source] {
            let block = blocks[target];
            if new_ids[block] == usize::MAX {
                budget.states(order.len() + 1)?;
                new_ids[block] = order.len();
                order.push(block);
            }
            append(&mut row, (lo, hi, new_ids[block]));
        }
        edge_count = edge_count
            .checked_add(row.len())
            .ok_or(LanguageError::Resource {
                resource: "transitions",
                limit: budget.limits.max_transitions,
            })?;
        budget.transitions(edge_count)?;
        output.push(row);
        flags.push(accepting[source]);
        cursor += 1;
    }
    Ok(Dfa {
        transitions: Arc::new(output),
        accepting: flags,
        edge_count,
    })
}

fn constant(accepting: bool, limits: Limits) -> Result<Dfa, LanguageError> {
    let mut budget = Budget::new(limits)?;
    budget.states(1)?;
    budget.transitions(2)?;
    budget.spend(3)?;
    Ok(Dfa {
        transitions: Arc::new(vec![vec![
            (0, SURROGATE_LO - 1, 0),
            (SURROGATE_HI + 1, MAX_SCALAR, 0),
        ]]),
        accepting: vec![accepting],
        edge_count: 2,
    })
}

pub fn empty(limits: Limits) -> Result<Dfa, LanguageError> {
    constant(false, limits)
}
pub fn universal(limits: Limits) -> Result<Dfa, LanguageError> {
    constant(true, limits)
}

impl Dfa {
    fn check(&self, budget: &Budget) -> Result<(), LanguageError> {
        budget.states(self.accepting.len())?;
        budget.transitions(self.edge_count)
    }

    fn step(&self, state: usize, scalar: u32) -> usize {
        let row = &self.transitions[state];
        let position = row.partition_point(|&(lo, _, _)| lo <= scalar);
        row[position - 1].2
    }

    pub fn accepts(&self, text: &str, limits: Limits) -> Result<bool, LanguageError> {
        let mut budget = Budget::new(limits)?;
        self.check(&budget)?;
        budget.input_bytes(text.len())?;
        budget.spend(text.len())?;
        budget.spend(1)?;
        let mut state = 0;
        for character in text.chars() {
            budget.spend(1)?;
            state = self.step(state, character as u32);
        }
        Ok(self.accepting[state])
    }

    pub fn is_empty(&self, limits: Limits) -> Result<bool, LanguageError> {
        let mut budget = Budget::new(limits)?;
        self.check(&budget)?;
        budget.spend(1)?;
        // In a minimal reachable DFA the empty language is exactly one rejecting state.
        Ok(self.accepting.len() == 1 && !self.accepting[0])
    }

    pub fn complement(&self, limits: Limits) -> Result<Dfa, LanguageError> {
        let mut budget = Budget::new(limits)?;
        self.check(&budget)?;
        budget.spend(self.accepting.len())?;
        Ok(Dfa {
            transitions: Arc::clone(&self.transitions),
            accepting: self.accepting.iter().map(|value| !value).collect(),
            edge_count: self.edge_count,
        })
    }

    pub fn intersection(&self, other: &Dfa, limits: Limits) -> Result<Dfa, LanguageError> {
        self.product(other, limits, |a, b| a && b)
    }
    pub fn union(&self, other: &Dfa, limits: Limits) -> Result<Dfa, LanguageError> {
        self.product(other, limits, |a, b| a || b)
    }
    pub fn difference(&self, other: &Dfa, limits: Limits) -> Result<Dfa, LanguageError> {
        self.product(other, limits, |a, b| a && !b)
    }

    fn product(
        &self,
        other: &Dfa,
        limits: Limits,
        accept: fn(bool, bool) -> bool,
    ) -> Result<Dfa, LanguageError> {
        let mut budget = Budget::new(limits)?;
        self.check(&budget)?;
        other.check(&budget)?;
        budget.states(1)?;
        budget.spend(1)?;
        let mut pairs = vec![(0usize, 0usize)];
        let mut ids = HashMap::from([((0usize, 0usize), 0usize)]);
        let mut rows: Rows = Vec::new();
        let mut accepting = Vec::new();
        let mut cursor = 0;
        let mut edge_count = 0usize;
        while cursor < pairs.len() {
            budget.spend(1)?;
            let (left, right) = pairs[cursor];
            let a = &self.transitions[left];
            let b = &other.transitions[right];
            let (mut i, mut j) = (0, 0);
            let mut row: Vec<Transition> = Vec::new();
            while i < a.len() && j < b.len() {
                budget.spend(1)?;
                let (alo, ahi, at) = a[i];
                let (blo, bhi, bt) = b[j];
                let lo = alo.max(blo);
                let hi = ahi.min(bhi);
                if lo <= hi {
                    let key = (at, bt);
                    let target = if let Some(&id) = ids.get(&key) {
                        id
                    } else {
                        budget.states(pairs.len() + 1)?;
                        budget.spend(1)?;
                        let id = pairs.len();
                        pairs.push(key);
                        ids.insert(key, id);
                        id
                    };
                    let merges = row
                        .last()
                        .is_some_and(|last| last.1 + 1 == lo && last.2 == target);
                    if !merges {
                        edge_count = edge_count.checked_add(1).ok_or(LanguageError::Resource {
                            resource: "transitions",
                            limit: limits.max_transitions,
                        })?;
                        budget.transitions(edge_count)?;
                    }
                    append(&mut row, (lo, hi, target));
                }
                if ahi <= bhi {
                    i += 1;
                }
                if bhi <= ahi {
                    j += 1;
                }
            }
            rows.push(row);
            accepting.push(accept(self.accepting[left], other.accepting[right]));
            cursor += 1;
        }
        minimize(rows, accepting, 0, &mut budget)
    }

    pub fn witness(&self, limits: Limits) -> Result<Option<String>, LanguageError> {
        let mut budget = Budget::new(limits)?;
        self.check(&budget)?;
        budget.spend(1)?;
        if self.accepting[0] {
            return Ok(Some(String::new()));
        }
        if self.accepting.len() == 1 {
            return Ok(None);
        }
        let n = self.accepting.len();
        budget.spend(n)?;
        // Keep predecessors, not a copied prefix string per visited state.
        let mut previous = vec![None; n];
        previous[0] = Some((0usize, 0u32));
        let mut queue = VecDeque::from([0usize]);
        while let Some(state) = queue.pop_front() {
            for &(lo, _, target) in &self.transitions[state] {
                budget.spend(1)?;
                if previous[target].is_some() {
                    continue;
                }
                previous[target] = Some((state, lo));
                if self.accepting[target] {
                    let mut cursor = target;
                    let mut byte_count = 0usize;
                    let mut characters = Vec::new();
                    while cursor != 0 {
                        budget.spend(1)?;
                        let (parent, scalar) =
                            previous[cursor].expect("visited witness predecessor");
                        let character = char::from_u32(scalar).expect("validated scalar range");
                        byte_count = byte_count.checked_add(character.len_utf8()).ok_or(
                            LanguageError::Resource {
                                resource: "input_bytes",
                                limit: limits.max_input_bytes,
                            },
                        )?;
                        budget.input_bytes(byte_count)?;
                        characters.push(character);
                        cursor = parent;
                    }
                    budget.spend(byte_count)?;
                    let mut result = String::with_capacity(byte_count);
                    result.extend(characters.into_iter().rev());
                    return Ok(Some(result));
                }
                queue.push_back(target);
            }
        }
        Ok(None)
    }

    pub fn cardinality(&self, bound: usize, limits: Limits) -> Result<usize, LanguageError> {
        let mut budget = Budget::new(limits)?;
        self.check(&budget)?;
        budget.spend(1)?;
        if bound == 0 {
            return Ok(0);
        }
        let n = self.accepting.len();
        if n == 1 {
            return Ok(if self.accepting[0] { bound } else { 0 });
        }
        budget.spend(n)?;
        let mut reverse = vec![Vec::new(); n];
        budget.spend(self.edge_count)?;
        for (source, row) in self.transitions.iter().enumerate() {
            for &(_, _, target) in row {
                reverse[target].push(source);
            }
        }
        budget.spend(n)?;
        let mut productive = self.accepting.clone();
        let mut stack: Vec<usize> = (0..n).filter(|&state| productive[state]).collect();
        while let Some(state) = stack.pop() {
            for &parent in &reverse[state] {
                budget.spend(1)?;
                if !productive[parent] {
                    productive[parent] = true;
                    stack.push(parent);
                }
            }
        }
        if !productive[0] {
            return Ok(0);
        }
        budget.spend(n)?;
        let mut indegree = vec![0usize; n];
        let mut live_count = 0;
        for (state, row) in self.transitions.iter().enumerate() {
            budget.spend(1)?;
            if !productive[state] {
                continue;
            }
            live_count += 1;
            budget.spend(row.len())?;
            for &(_, _, target) in row {
                if productive[target] {
                    indegree[target] += 1;
                }
            }
        }
        budget.spend(n)?;
        let mut queue: VecDeque<usize> = (0..n)
            .filter(|&state| productive[state] && indegree[state] == 0)
            .collect();
        let mut order = Vec::new();
        while let Some(state) = queue.pop_front() {
            budget.spend(1)?;
            order.push(state);
            for &(_, _, target) in &self.transitions[state] {
                budget.spend(1)?;
                if productive[target] {
                    indegree[target] -= 1;
                    if indegree[target] == 0 {
                        queue.push_back(target);
                    }
                }
            }
        }
        if order.len() != live_count {
            return Ok(bound);
        }
        budget.spend(n)?;
        let mut counts = vec![0usize; n];
        for &state in order.iter().rev() {
            let mut count = usize::from(self.accepting[state]);
            for &(lo, hi, target) in &self.transitions[state] {
                budget.spend(1)?;
                if productive[target] {
                    count = count
                        .saturating_add(counts[target].saturating_mul((hi - lo + 1) as usize))
                        .min(bound);
                }
            }
            counts[state] = count;
        }
        Ok(counts[0])
    }
}

struct JsonOutput {
    bytes: Vec<u8>,
}

impl JsonOutput {
    fn push(&mut self, bytes: &[u8], budget: &mut Budget) -> Result<(), LanguageError> {
        let count = self
            .bytes
            .len()
            .checked_add(bytes.len())
            .ok_or(LanguageError::Resource {
                resource: "input_bytes",
                limit: budget.limits.max_input_bytes,
            })?;
        budget.input_bytes(count)?;
        budget.spend(bytes.len())?;
        self.bytes.extend_from_slice(bytes);
        Ok(())
    }

    fn number(&mut self, mut number: usize, budget: &mut Budget) -> Result<(), LanguageError> {
        let mut digits = [0u8; 20];
        let mut cursor = digits.len();
        loop {
            cursor -= 1;
            digits[cursor] = b'0' + (number % 10) as u8;
            number /= 10;
            if number == 0 {
                break;
            }
        }
        self.push(&digits[cursor..], budget)
    }
}

impl Dfa {
    pub fn canonical_json(&self, limits: Limits) -> Result<Vec<u8>, LanguageError> {
        let mut budget = Budget::new(limits)?;
        self.check(&budget)?;
        budget.spend(self.accepting.len())?;
        let mut output = JsonOutput { bytes: Vec::new() };
        output.push(b"{\"accepting\":[", &mut budget)?;
        let mut comma = false;
        for (state, &accepting) in self.accepting.iter().enumerate() {
            if accepting {
                if comma {
                    output.push(b",", &mut budget)?;
                }
                output.number(state, &mut budget)?;
                comma = true;
            }
        }
        output.push(b"],\"alphabet\":\"unicode_scalars\",\"schema_version\":1,\"start\":0,\"transitions\":[", &mut budget)?;
        comma = false;
        for (state, row) in self.transitions.iter().enumerate() {
            for &(lo, hi, target) in row {
                if comma {
                    output.push(b",", &mut budget)?;
                }
                output.push(b"[", &mut budget)?;
                output.number(state, &mut budget)?;
                output.push(b",", &mut budget)?;
                output.number(lo as usize, &mut budget)?;
                output.push(b",", &mut budget)?;
                output.number(hi as usize, &mut budget)?;
                output.push(b",", &mut budget)?;
                output.number(target, &mut budget)?;
                output.push(b"]", &mut budget)?;
                comma = true;
            }
        }
        output.push(b"]}", &mut budget)?;
        Ok(output.bytes)
    }

    pub fn from_json(data: &[u8], limits: Limits) -> Result<Dfa, LanguageError> {
        use serde::de::DeserializeSeed;
        let mut budget = Budget::new(limits)?;
        budget.input_bytes(data.len())?;
        budget.spend(data.len())?;
        let mut resource_error = None;
        let mut decoder = serde_json::Deserializer::from_slice(data);
        let parsed = WireSeed {
            limits,
            resource_error: &mut resource_error,
        }
        .deserialize(&mut decoder);
        let wire = match parsed {
            Ok(wire) => wire,
            Err(error) => return Err(resource_error.unwrap_or_else(|| invalid(error.to_string()))),
        };
        decoder.end().map_err(|error| invalid(error.to_string()))?;
        if wire.schema_version != 1 || wire.alphabet != "unicode_scalars" || wire.start != 0 {
            return Err(invalid("unsupported language schema, alphabet or start"));
        }
        if wire.transitions.is_empty() {
            return Err(invalid("missing transition states"));
        }
        let mut rows: Rows = Vec::new();
        let mut expected_lower = 0u64;
        let mut current_source = 0usize;
        for [source, lo, hi, target] in wire.transitions {
            budget.spend(1)?;
            if source > i64::MAX as u64 || target > i64::MAX as u64 {
                return Err(invalid("state counters exceed signed int64"));
            }
            let source = usize::try_from(source)
                .map_err(|_| invalid("state counter exceeds platform range"))?;
            let target = usize::try_from(target)
                .map_err(|_| invalid("state counter exceeds platform range"))?;
            if rows.is_empty() || source != current_source {
                if !rows.is_empty()
                    && (source != current_source + 1 || expected_lower != MAX_SCALAR as u64 + 1)
                {
                    return Err(invalid("states are not contiguous, complete and ordered"));
                }
                if rows.is_empty() && source != 0 {
                    return Err(invalid("first state must be zero"));
                }
                budget.states(rows.len() + 1)?;
                rows.push(Vec::new());
                current_source = source;
                expected_lower = 0;
            }
            if lo != expected_lower
                || lo > hi
                || hi > MAX_SCALAR as u64
                || (lo <= SURROGATE_HI as u64 && hi >= SURROGATE_LO as u64)
            {
                return Err(invalid(
                    "transition ranges contain a gap, overlap or invalid scalar",
                ));
            }
            let row = &mut rows[source];
            if row
                .last()
                .is_some_and(|previous| previous.1 as u64 + 1 == lo && previous.2 == target)
            {
                return Err(invalid("adjacent equal-target ranges must be coalesced"));
            }
            row.push((lo as u32, hi as u32, target));
            expected_lower = hi + 1;
            if expected_lower == SURROGATE_LO as u64 {
                expected_lower = SURROGATE_HI as u64 + 1;
            }
        }
        if expected_lower != MAX_SCALAR as u64 + 1 {
            return Err(invalid("incomplete final state"));
        }
        let n = rows.len();
        budget.spend(n)?;
        let mut accepting = vec![false; n];
        let mut previous = None;
        for id in wire.accepting {
            budget.spend(1)?;
            if id >= n as u64 || previous.is_some_and(|value| id <= value) {
                return Err(invalid(
                    "accepting states must be sorted, unique and in range",
                ));
            }
            accepting[id as usize] = true;
            previous = Some(id);
        }
        let edge_count = total_edges(&rows, &mut budget)?;
        budget.spend(n)?;
        let mut seen = vec![false; n];
        seen[0] = true;
        let mut discovered = 1;
        for (source, row) in rows.iter().enumerate() {
            if !seen[source] {
                return Err(invalid("unreachable state"));
            }
            for &(_, _, target) in row {
                budget.spend(1)?;
                if target >= n {
                    return Err(invalid("transition target outside state range"));
                }
                if !seen[target] {
                    if target != discovered {
                        return Err(invalid("states must use canonical BFS numbering"));
                    }
                    seen[target] = true;
                    discovered += 1;
                }
            }
        }
        let (_, blocks) = partition(&rows, &accepting, &mut budget)?;
        if blocks != n {
            return Err(invalid("equivalent states: automaton is not minimal"));
        }
        Ok(Dfa {
            transitions: Arc::new(rows),
            accepting,
            edge_count,
        })
    }
}

struct Wire {
    schema_version: u64,
    alphabet: String,
    start: u64,
    accepting: Vec<u64>,
    transitions: Vec<[u64; 4]>,
}

struct BoundedSequence<'a, T> {
    limit: usize,
    resource: &'static str,
    resource_error: &'a mut Option<LanguageError>,
    item: std::marker::PhantomData<T>,
}

impl<'de, T: serde::Deserialize<'de>> serde::de::DeserializeSeed<'de> for BoundedSequence<'_, T> {
    type Value = Vec<T>;
    fn deserialize<D: serde::Deserializer<'de>>(self, decoder: D) -> Result<Self::Value, D::Error> {
        decoder.deserialize_seq(self)
    }
}

impl<'de, T: serde::Deserialize<'de>> serde::de::Visitor<'de> for BoundedSequence<'_, T> {
    type Value = Vec<T>;
    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("a bounded integer array")
    }
    fn visit_seq<A: serde::de::SeqAccess<'de>>(
        self,
        mut array: A,
    ) -> Result<Self::Value, A::Error> {
        let mut values = Vec::new();
        while let Some(value) = array.next_element()? {
            if values.len() == self.limit {
                *self.resource_error = Some(LanguageError::Resource {
                    resource: self.resource,
                    limit: self.limit,
                });
                return Err(serde::de::Error::custom("language resource limit exceeded"));
            }
            values.push(value);
        }
        Ok(values)
    }
}

struct WireSeed<'a> {
    limits: Limits,
    resource_error: &'a mut Option<LanguageError>,
}

impl<'de> serde::de::DeserializeSeed<'de> for WireSeed<'_> {
    type Value = Wire;
    fn deserialize<D: serde::Deserializer<'de>>(self, decoder: D) -> Result<Wire, D::Error> {
        decoder.deserialize_map(self)
    }
}

impl<'de> serde::de::Visitor<'de> for WireSeed<'_> {
    type Value = Wire;
    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("a language-1 object")
    }
    fn visit_map<A: serde::de::MapAccess<'de>>(self, mut map: A) -> Result<Wire, A::Error> {
        use serde::de::Error;
        let (mut schema_version, mut alphabet, mut start, mut accepting, mut transitions) =
            (None, None, None, None, None);
        while let Some(key) = map.next_key::<String>()? {
            match key.as_str() {
                "schema_version" if schema_version.is_none() => {
                    schema_version = Some(map.next_value()?)
                }
                "alphabet" if alphabet.is_none() => alphabet = Some(map.next_value()?),
                "start" if start.is_none() => start = Some(map.next_value()?),
                "accepting" if accepting.is_none() => {
                    accepting = Some(map.next_value_seed(BoundedSequence {
                        limit: self.limits.max_states,
                        resource: "states",
                        resource_error: &mut *self.resource_error,
                        item: std::marker::PhantomData,
                    })?)
                }
                "transitions" if transitions.is_none() => {
                    transitions = Some(map.next_value_seed(BoundedSequence {
                        limit: self.limits.max_transitions,
                        resource: "transitions",
                        resource_error: &mut *self.resource_error,
                        item: std::marker::PhantomData,
                    })?)
                }
                _ => return Err(A::Error::custom("duplicate or unknown language field")),
            }
        }
        Ok(Wire {
            schema_version: schema_version
                .ok_or_else(|| A::Error::missing_field("schema_version"))?,
            alphabet: alphabet.ok_or_else(|| A::Error::missing_field("alphabet"))?,
            start: start.ok_or_else(|| A::Error::missing_field("start"))?,
            accepting: accepting.ok_or_else(|| A::Error::missing_field("accepting"))?,
            transitions: transitions.ok_or_else(|| A::Error::missing_field("transitions"))?,
        })
    }
}
