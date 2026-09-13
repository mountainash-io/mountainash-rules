//! Assertion-aware Unicode-scalar regex compilation with bounded construction.
use super::automaton::minimize;
use super::{
    Budget, Dfa, Flags, LanguageError, Limits, LiteralKind, Rows, MAX_SCALAR, SURROGATE_HI,
    SURROGATE_LO,
};
use regex_syntax::{
    ast,
    hir::{Class, Hir, HirKind, Look},
    ParserBuilder,
};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::{Arc, LazyLock};
/// Classification of a Unicode scalar value (or a beginning/end-of-input
/// pseudo-position) used to resolve zero-width `Look` assertions. `Wa`
/// (ASCII word) is a refinement of `Wu` (Unicode word but not ASCII word);
/// `Cr`/`Lf` are singleton classes needed for the CRLF-aware assertions.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
enum Ctx {
    Bof,
    Eof,
    Cr,
    Lf,
    Wa,
    Wu,
    Ot,
}

impl Ctx {
    fn is_word_ascii(self) -> bool {
        matches!(self, Ctx::Wa)
    }
    fn is_word_unicode(self) -> bool {
        matches!(self, Ctx::Wa | Ctx::Wu)
    }
}

/// Resolve a `regex_syntax::hir::Look` assertion given the classification of
/// the character immediately before (`prev`) and after (`cur`) the position
/// being tested. `prev == Ctx::Bof` means "no preceding character" (start of
/// haystack); `cur == Ctx::Eof` means "no following character" (end of
/// haystack). All 18 `Look` variants are handled explicitly.
fn look_holds(look: Look, prev: Ctx, cur: Ctx) -> bool {
    use Look::*;
    match look {
        Start => prev == Ctx::Bof,
        End => cur == Ctx::Eof,
        StartLF => prev == Ctx::Bof || prev == Ctx::Lf,
        EndLF => cur == Ctx::Eof || cur == Ctx::Lf,
        StartCRLF => prev == Ctx::Bof || prev == Ctx::Lf || (prev == Ctx::Cr && cur != Ctx::Lf),
        EndCRLF => cur == Ctx::Eof || cur == Ctx::Cr || (cur == Ctx::Lf && prev != Ctx::Cr),
        WordAscii => prev.is_word_ascii() != cur.is_word_ascii(),
        WordAsciiNegate => prev.is_word_ascii() == cur.is_word_ascii(),
        WordUnicode => prev.is_word_unicode() != cur.is_word_unicode(),
        WordUnicodeNegate => prev.is_word_unicode() == cur.is_word_unicode(),
        WordStartAscii => !prev.is_word_ascii() && cur.is_word_ascii(),
        WordEndAscii => prev.is_word_ascii() && !cur.is_word_ascii(),
        WordStartUnicode => !prev.is_word_unicode() && cur.is_word_unicode(),
        WordEndUnicode => prev.is_word_unicode() && !cur.is_word_unicode(),
        WordStartHalfAscii => !prev.is_word_ascii(),
        WordEndHalfAscii => !cur.is_word_ascii(),
        WordStartHalfUnicode => !prev.is_word_unicode(),
        WordEndHalfUnicode => !cur.is_word_unicode(),
    }
}

type Ranges = Arc<[(u32, u32)]>;

fn contains(ranges: &[(u32, u32)], scalar: u32) -> bool {
    let position = ranges.partition_point(|&(lo, _)| lo <= scalar);
    position > 0 && scalar <= ranges[position - 1].1
}

fn unicode_words(budget: &mut Budget) -> Result<&'static [(u32, u32)], LanguageError> {
    static WORDS: LazyLock<Vec<(u32, u32)>> = LazyLock::new(|| {
        let hir = ParserBuilder::new()
            .build()
            .parse(r"\w")
            .expect("pinned Unicode word class");
        let HirKind::Class(Class::Unicode(class)) = hir.kind() else {
            unreachable!("Unicode class")
        };
        class
            .ranges()
            .iter()
            .map(|range| (range.start() as u32, range.end() as u32))
            .collect()
    });
    // Bound the cold table expansion as well as warm calls deterministically.
    budget.spend(MAX_SCALAR as usize + 1)?;
    Ok(&WORDS)
}

/// Charge a conservative Unicode-domain expansion before HIR translation.
/// ASCII literals do not pay for a whole Unicode table. Classes and class
/// algebra can expand over the entire scalar domain, even from short input.
struct Preflight<'a> {
    budget: &'a mut Budget,
}
impl ast::Visitor for Preflight<'_> {
    type Output = ();
    type Err = LanguageError;
    fn finish(self) -> Result<(), LanguageError> {
        Ok(())
    }
    fn visit_pre(&mut self, node: &ast::Ast) -> Result<(), LanguageError> {
        self.budget.spend(match node {
            ast::Ast::ClassUnicode(_) | ast::Ast::ClassPerl(_) | ast::Ast::ClassBracketed(_) => {
                MAX_SCALAR as usize + 1
            }
            // Pinned Unicode simple-fold classes have at most four members;
            // allow room for translation bookkeeping, not just final ranges.
            ast::Ast::Literal(_) => 128,
            _ => 1,
        })
    }
    fn visit_class_set_item_pre(&mut self, node: &ast::ClassSetItem) -> Result<(), LanguageError> {
        self.budget.spend(match node {
            ast::ClassSetItem::Unicode(_)
            | ast::ClassSetItem::Perl(_)
            | ast::ClassSetItem::Ascii(_)
            | ast::ClassSetItem::Range(_)
            | ast::ClassSetItem::Bracketed(_) => MAX_SCALAR as usize + 1,
            _ => 128,
        })
    }
    fn visit_class_set_binary_op_pre(
        &mut self,
        _: &ast::ClassSetBinaryOp,
    ) -> Result<(), LanguageError> {
        self.budget.spend(MAX_SCALAR as usize + 1)
    }
}

enum Inst {
    Char(Ranges, usize),
    Split(Vec<usize>),
    Look(Look, usize),
    Match,
}

struct Nfa<'a> {
    instructions: Vec<Inst>,
    budget: &'a mut Budget,
}
impl Nfa<'_> {
    fn push(&mut self, instruction: Inst) -> Result<usize, LanguageError> {
        self.budget.nfa_states(self.instructions.len() + 1)?;
        self.budget.spend(1)?;
        let id = self.instructions.len();
        self.instructions.push(instruction);
        Ok(id)
    }
    fn compile(&mut self, hir: &Hir, out: usize, depth: usize) -> Result<usize, LanguageError> {
        // HIR may add a small number of structural layers to the bounded AST.
        Budget::check("nesting", depth, 512)?;
        self.budget.spend(1)?;
        match hir.kind() {
            HirKind::Empty => Ok(out),
            HirKind::Literal(literal) => {
                let text = std::str::from_utf8(&literal.0)
                    .map_err(|error| LanguageError::Syntax(error.to_string()))?;
                self.text(text, out)
            }
            HirKind::Class(class) => {
                let count = match class {
                    Class::Unicode(c) => c.ranges().len(),
                    Class::Bytes(c) => c.ranges().len(),
                };
                self.budget.transitions(count)?;
                self.budget.spend(count)?;
                let ranges: Vec<_> = match class {
                    Class::Unicode(c) => c
                        .ranges()
                        .iter()
                        .map(|r| (r.start() as u32, r.end() as u32))
                        .collect(),
                    Class::Bytes(c) => {
                        if c.ranges().iter().any(|r| r.end() > 127) {
                            return Err(LanguageError::Syntax(
                                "non-ASCII byte languages are not scalar languages".into(),
                            ));
                        }
                        c.ranges()
                            .iter()
                            .map(|r| (r.start() as u32, r.end() as u32))
                            .collect()
                    }
                };
                self.push(Inst::Char(ranges.into(), out))
            }
            HirKind::Look(look) => self.push(Inst::Look(*look, out)),
            HirKind::Capture(capture) => self.compile(&capture.sub, out, depth + 1),
            HirKind::Concat(children) => {
                let mut next = out;
                for child in children.iter().rev() {
                    next = self.compile(child, next, depth + 1)?;
                }
                Ok(next)
            }
            HirKind::Alternation(children) => {
                self.budget.spend(children.len())?;
                let mut targets = Vec::with_capacity(children.len());
                for child in children {
                    targets.push(self.compile(child, out, depth + 1)?);
                }
                self.push(Inst::Split(targets))
            }
            HirKind::Repetition(repetition) => {
                let mut next = out;
                match repetition.max {
                    None => {
                        let loop_id = self.push(Inst::Split(Vec::new()))?;
                        let body = self.compile(&repetition.sub, loop_id, depth + 1)?;
                        self.instructions[loop_id] = Inst::Split(vec![body, out]);
                        next = loop_id;
                    }
                    Some(maximum) => {
                        for _ in repetition.min..maximum {
                            self.budget.spend(1)?;
                            let body = self.compile(&repetition.sub, next, depth + 1)?;
                            next = self.push(Inst::Split(vec![body, next]))?;
                        }
                    }
                }
                for _ in 0..repetition.min {
                    self.budget.spend(1)?;
                    next = self.compile(&repetition.sub, next, depth + 1)?;
                }
                Ok(next)
            }
        }
    }
    fn text(&mut self, text: &str, out: usize) -> Result<usize, LanguageError> {
        let mut next = out;
        for scalar in text.chars().rev() {
            self.budget.nfa_states(self.instructions.len() + 1)?;
            self.budget.spend(1)?;
            next = self.push(Inst::Char(
                Arc::from([(scalar as u32, scalar as u32)]),
                next,
            ))?;
        }
        Ok(next)
    }
    fn search_prefix(&mut self, entry: usize) -> Result<usize, LanguageError> {
        let start = self.push(Inst::Split(Vec::new()))?;
        let any = self.push(Inst::Char(
            Arc::from([(0, SURROGATE_LO - 1), (SURROGATE_HI + 1, MAX_SCALAR)]),
            start,
        ))?;
        self.instructions[start] = Inst::Split(vec![entry, any]);
        Ok(start)
    }
}

fn closure(
    insts: &[Inst],
    frontier: &[usize],
    context: Option<(Ctx, Ctx)>,
    budget: &mut Budget,
) -> Result<Vec<usize>, LanguageError> {
    budget.spend(frontier.len())?;
    let mut stack = frontier.to_vec();
    let mut seen = HashSet::new();
    let mut active = Vec::new();
    while let Some(id) = stack.pop() {
        budget.spend(1)?;
        if !seen.insert(id) {
            continue;
        }
        match &insts[id] {
            Inst::Split(targets) => {
                budget.spend(targets.len())?;
                stack.extend_from_slice(targets);
            }
            Inst::Look(look, target) => match context {
                Some((prev, current)) if look_holds(*look, prev, current) => stack.push(*target),
                None => active.push(id),
                _ => {}
            },
            _ => active.push(id),
        }
    }
    budget.spend(
        active
            .len()
            .saturating_mul(active.len().max(1).ilog2() as usize + 1),
    )?;
    active.sort_unstable();
    Ok(active)
}

fn alphabet(insts: &[Inst], budget: &mut Budget) -> Result<Vec<(u32, u32, Ctx)>, LanguageError> {
    let (mut cr, mut lf, mut ascii, mut unicode) = (false, false, false, false);
    budget.spend(insts.len())?;
    for instruction in insts {
        if let Inst::Look(look, _) = instruction {
            use Look::*;
            match look {
                StartCRLF | EndCRLF => {
                    cr = true;
                    lf = true;
                }
                StartLF | EndLF => lf = true,
                WordAscii | WordAsciiNegate | WordStartAscii | WordEndAscii
                | WordStartHalfAscii | WordEndHalfAscii => ascii = true,
                WordUnicode | WordUnicodeNegate | WordStartUnicode | WordEndUnicode
                | WordStartHalfUnicode | WordEndHalfUnicode => unicode = true,
                Start | End => {}
            }
        }
    }
    let words = if unicode { unicode_words(budget)? } else { &[] };
    let ascii_words = [(48, 57), (65, 90), (95, 95), (97, 122)];
    budget.spend(4)?;
    let mut cuts = BTreeSet::from([0, SURROGATE_LO, SURROGATE_HI + 1, MAX_SCALAR + 1]);
    let mut add = |lo, hi, budget: &mut Budget| -> Result<(), LanguageError> {
        budget.spend(2)?;
        cuts.insert(lo);
        cuts.insert(hi + 1);
        Ok(())
    };
    for instruction in insts {
        if let Inst::Char(ranges, _) = instruction {
            for &(lo, hi) in ranges.iter() {
                add(lo, hi, budget)?;
            }
        }
    }
    if cr {
        add(13, 13, budget)?;
    }
    if lf {
        add(10, 10, budget)?;
    }
    // Unicode classes include ASCII word characters; refine both classifications.
    if ascii || unicode {
        for &(lo, hi) in &ascii_words {
            add(lo, hi, budget)?;
        }
    }
    for &(lo, hi) in words {
        add(lo, hi, budget)?;
    }
    budget.spend(cuts.len())?;
    let cuts: Vec<_> = cuts.into_iter().collect();
    let mut result = Vec::new();
    for pair in cuts.windows(2) {
        budget.spend(1)?;
        let (lo, hi) = (pair[0], pair[1] - 1);
        if lo == SURROGATE_LO {
            continue;
        }
        let context = if cr && lo == 13 {
            Ctx::Cr
        } else if lf && lo == 10 {
            Ctx::Lf
        } else if (ascii || unicode) && contains(&ascii_words, lo) {
            Ctx::Wa
        } else if unicode && contains(words, lo) {
            Ctx::Wu
        } else {
            Ctx::Ot
        };
        result.push((lo, hi, context));
    }
    Ok(result)
}

fn determinize(
    insts: &[Inst],
    entry: usize,
    units: &[(u32, u32, Ctx)],
    budget: &mut Budget,
) -> Result<Dfa, LanguageError> {
    type Key = (Ctx, Arc<[usize]>);
    let initial = closure(insts, &[entry], None, budget)?;
    budget.states(1)?;
    budget.spend(initial.len() + 1)?;
    let key: Key = (Ctx::Bof, initial.into());
    let mut states = vec![Some(key.clone())];
    let mut ids = HashMap::from([(key, 0usize)]);
    let mut sink = None;
    let mut rows: Rows = Vec::new();
    let mut accepting = Vec::new();
    let mut edge_count = 0usize;
    let mut cursor = 0;
    while cursor < states.len() {
        budget.spend(1)?;
        let mut row: Vec<(u32, u32, usize)> = Vec::new();
        if let Some((previous, frontier)) = states[cursor].clone() {
            for &(lo, hi, current) in units {
                budget.spend(1)?;
                let active = closure(insts, &frontier, Some((previous, current)), budget)?;
                budget.spend(active.len())?;
                let target = if active.iter().any(|&id| matches!(insts[id], Inst::Match)) {
                    if let Some(id) = sink {
                        id
                    } else {
                        budget.states(states.len() + 1)?;
                        budget.spend(1)?;
                        let id = states.len();
                        states.push(None);
                        sink = Some(id);
                        id
                    }
                } else {
                    budget.spend(active.len())?;
                    let next: Vec<_> = active
                        .iter()
                        .filter_map(|&id| match &insts[id] {
                            Inst::Char(ranges, target) if contains(ranges, lo) => Some(*target),
                            _ => None,
                        })
                        .collect();
                    let next = closure(insts, &next, None, budget)?;
                    budget.spend(next.len())?;
                    let key: Key = (current, next.into());
                    if let Some(&id) = ids.get(&key) {
                        id
                    } else {
                        budget.states(states.len() + 1)?;
                        budget.spend(1)?;
                        let id = states.len();
                        states.push(Some(key.clone()));
                        ids.insert(key, id);
                        id
                    }
                };
                if let Some(last) = row.last_mut() {
                    if last.1 + 1 == lo && last.2 == target {
                        last.1 = hi;
                        continue;
                    }
                }
                edge_count = edge_count.checked_add(1).ok_or(LanguageError::Resource {
                    resource: "transitions",
                    limit: budget.limits.max_transitions,
                })?;
                budget.transitions(edge_count)?;
                row.push((lo, hi, target));
            }
            let end = closure(insts, &frontier, Some((previous, Ctx::Eof)), budget)?;
            budget.spend(end.len())?;
            accepting.push(end.iter().any(|&id| matches!(insts[id], Inst::Match)));
        } else {
            edge_count = edge_count.checked_add(2).ok_or(LanguageError::Resource {
                resource: "transitions",
                limit: budget.limits.max_transitions,
            })?;
            budget.transitions(edge_count)?;
            budget.spend(2)?;
            row.extend([
                (0, SURROGATE_LO - 1, cursor),
                (SURROGATE_HI + 1, MAX_SCALAR, cursor),
            ]);
            accepting.push(true);
        }
        rows.push(row);
        cursor += 1;
    }
    minimize(rows, accepting, 0, budget)
}

pub fn compile(pattern: &str, flags: Flags, limits: Limits) -> Result<Dfa, LanguageError> {
    let mut budget = Budget::new(limits)?;
    budget.input_bytes(pattern.len())?;
    budget.spend(pattern.len())?;
    let ast = ast::parse::ParserBuilder::new()
        .nest_limit(limits.max_nesting as u32)
        .ignore_whitespace(flags.ignore_whitespace)
        .build()
        .parse(pattern)
        .map_err(|error| {
            if matches!(error.kind(), ast::ErrorKind::NestLimitExceeded(_)) {
                LanguageError::Resource {
                    resource: "nesting",
                    limit: limits.max_nesting,
                }
            } else {
                LanguageError::Syntax(error.to_string())
            }
        })?;
    ast::visit(
        &ast,
        Preflight {
            budget: &mut budget,
        },
    )?;
    let hir = regex_syntax::hir::translate::TranslatorBuilder::new()
        .utf8(true)
        .unicode(flags.unicode)
        .case_insensitive(flags.case_insensitive)
        .multi_line(flags.multi_line)
        .dot_matches_new_line(flags.dot_matches_new_line)
        .crlf(flags.crlf)
        .swap_greed(flags.swap_greed)
        .build()
        .translate(pattern, &ast)
        .map_err(|error| LanguageError::Syntax(error.to_string()))?;
    let mut nfa = Nfa {
        instructions: Vec::new(),
        budget: &mut budget,
    };
    let matched = nfa.push(Inst::Match)?;
    let entry = nfa.compile(&hir, matched, 0)?;
    let entry = nfa.search_prefix(entry)?;
    let instructions = nfa.instructions;
    let units = alphabet(&instructions, &mut budget)?;
    determinize(&instructions, entry, &units, &mut budget)
}

pub fn literal(text: &str, kind: LiteralKind, limits: Limits) -> Result<Dfa, LanguageError> {
    let mut budget = Budget::new(limits)?;
    budget.input_bytes(text.len())?;
    budget.spend(text.len())?;
    let mut nfa = Nfa {
        instructions: Vec::new(),
        budget: &mut budget,
    };
    let mut out = nfa.push(Inst::Match)?;
    if matches!(kind, LiteralKind::Exact | LiteralKind::Suffix) {
        out = nfa.push(Inst::Look(Look::End, out))?;
    }
    let mut entry = nfa.text(text, out)?;
    if matches!(kind, LiteralKind::Exact | LiteralKind::Prefix) {
        entry = nfa.push(Inst::Look(Look::Start, entry))?;
    } else {
        entry = nfa.search_prefix(entry)?;
    }
    let instructions = nfa.instructions;
    let units = alphabet(&instructions, &mut budget)?;
    determinize(&instructions, entry, &units, &mut budget)
}
