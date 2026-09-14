//! Exact Unicode-scalar languages, independent of Python and DataFrame backends.

use std::error::Error;
use std::fmt;

#[path = "automaton.rs"]
mod automaton;
#[path = "compiler.rs"]
mod compiler;

pub use automaton::{empty, universal, Dfa};
pub use compiler::{compile, literal};

pub(crate) const SURROGATE_LO: u32 = 0xD800;
pub(crate) const SURROGATE_HI: u32 = 0xDFFF;
pub(crate) const MAX_SCALAR: u32 = 0x10FFFF;
pub(crate) type Transition = (u32, u32, usize);
pub(crate) type Rows = Vec<Vec<Transition>>;

/// Explicit resource ceilings; zero is an exhaustion request, never a default.
#[derive(Clone, Copy, Debug)]
pub struct Limits {
    pub max_input_bytes: usize,
    pub max_nesting: usize,
    pub max_nfa_states: usize,
    pub max_states: usize,
    pub max_transitions: usize,
    pub max_work: usize,
}

/// Ambient Rust-regex search flags. Scoped pattern flags retain their meaning.
#[derive(Clone, Copy, Debug)]
pub struct Flags {
    pub case_insensitive: bool,
    pub multi_line: bool,
    pub dot_matches_new_line: bool,
    pub crlf: bool,
    pub swap_greed: bool,
    pub unicode: bool,
    pub ignore_whitespace: bool,
}

impl Default for Flags {
    fn default() -> Self {
        Self {
            case_insensitive: false,
            multi_line: false,
            dot_matches_new_line: false,
            crlf: false,
            swap_greed: false,
            unicode: true,
            ignore_whitespace: false,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub enum LiteralKind {
    Exact,
    Prefix,
    Suffix,
    Contains,
}

/// Invalid syntax/wire is distinct from exhaustion of a complete operation.
#[derive(Debug)]
pub enum LanguageError {
    Syntax(String),
    InvalidWire(String),
    Resource {
        resource: &'static str,
        limit: usize,
    },
}

impl fmt::Display for LanguageError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Syntax(message) | Self::InvalidWire(message) => formatter.write_str(message),
            Self::Resource { resource, limit } => {
                write!(formatter, "language {resource} limit exceeded ({limit})")
            }
        }
    }
}

impl Error for LanguageError {}

/// One budget is shared by all phases of a single public operation.
pub(crate) struct Budget {
    pub(crate) limits: Limits,
    remaining: usize,
}

impl Budget {
    pub(crate) fn new(limits: Limits) -> Result<Self, LanguageError> {
        if limits.max_nesting > 256 {
            return Err(LanguageError::Resource {
                resource: "nesting",
                limit: 256,
            });
        }
        Ok(Self {
            limits,
            remaining: limits.max_work,
        })
    }

    /// Charge before the bounded work/allocation, not after materialization.
    pub(crate) fn spend(&mut self, units: usize) -> Result<(), LanguageError> {
        self.remaining = self
            .remaining
            .checked_sub(units)
            .ok_or(LanguageError::Resource {
                resource: "work",
                limit: self.limits.max_work,
            })?;
        Ok(())
    }

    pub(crate) fn input_bytes(&self, count: usize) -> Result<(), LanguageError> {
        Self::check("input_bytes", count, self.limits.max_input_bytes)
    }

    pub(crate) fn states(&self, count: usize) -> Result<(), LanguageError> {
        Self::check("states", count, self.limits.max_states)
    }

    pub(crate) fn transitions(&self, count: usize) -> Result<(), LanguageError> {
        Self::check("transitions", count, self.limits.max_transitions)
    }

    pub(crate) fn nfa_states(&self, count: usize) -> Result<(), LanguageError> {
        Self::check("nfa_states", count, self.limits.max_nfa_states)
    }

    pub(crate) fn check(
        resource: &'static str,
        count: usize,
        limit: usize,
    ) -> Result<(), LanguageError> {
        if count > limit {
            return Err(LanguageError::Resource { resource, limit });
        }
        Ok(())
    }
}
