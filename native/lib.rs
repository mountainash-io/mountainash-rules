//! Python boundary for the backend-independent exact-language component.

mod language;

#[cfg(test)]
mod language_tests;

#[cfg(feature = "extension-module")]
mod python {

    use crate::language::{self, Dfa, Flags, LanguageError, Limits, LiteralKind};
    use pyo3::create_exception;
    use pyo3::exceptions::{PyRuntimeError, PyValueError};
    use pyo3::prelude::*;
    use pyo3::types::{PyBytes, PyModule, PyString};

    create_exception!(_native, LanguageSyntaxError, PyValueError);
    create_exception!(_native, LanguageWireError, PyValueError);
    create_exception!(_native, LanguageResourceError, PyRuntimeError);

    fn python_error(py: Python<'_>, error: LanguageError) -> PyErr {
        match error {
            LanguageError::Syntax(message) => LanguageSyntaxError::new_err(message),
            LanguageError::InvalidWire(message) => LanguageWireError::new_err(message),
            LanguageError::Resource { resource, limit } => {
                let error = LanguageResourceError::new_err(format!(
                    "language {resource} limit exceeded ({limit})"
                ));
                let value = error.value(py);
                if let Err(attribute_error) = value.setattr("resource", resource) {
                    return attribute_error;
                }
                if let Err(attribute_error) = value.setattr("limit", limit) {
                    return attribute_error;
                }
                error
            }
        }
    }

    fn input_text<'a>(text: &'a Bound<'_, PyString>, limits: Limits) -> PyResult<&'a str> {
        // Reject obviously oversized strings before Python creates a UTF-8 cache.
        // The core then enforces the exact UTF-8 byte count; the conversion itself
        // is bounded by four bytes per admitted scalar.
        if text.len()? > limits.max_input_bytes {
            return Err(python_error(
                text.py(),
                LanguageError::Resource {
                    resource: "input_bytes",
                    limit: limits.max_input_bytes,
                },
            ));
        }
        text.to_str()
    }

    #[pyclass(name = "Limits", frozen, module = "mountainash_rules._native")]
    struct PyLimits {
        inner: Limits,
    }

    #[pymethods]
    impl PyLimits {
        #[new]
        fn new(
            max_input_bytes: usize,
            max_nesting: usize,
            max_nfa_states: usize,
            max_states: usize,
            max_transitions: usize,
            max_work: usize,
        ) -> PyResult<Self> {
            if max_nesting > 256 {
                return Err(PyValueError::new_err("max_nesting must be at most 256"));
            }
            for value in [
                max_input_bytes,
                max_nfa_states,
                max_states,
                max_transitions,
                max_work,
            ] {
                if value as u128 > i64::MAX as u128 {
                    return Err(PyValueError::new_err(
                        "language limits must fit signed int64",
                    ));
                }
            }
            Ok(Self {
                inner: Limits {
                    max_input_bytes,
                    max_nesting,
                    max_nfa_states,
                    max_states,
                    max_transitions,
                    max_work,
                },
            })
        }
    }

    #[pyclass(name = "Dfa", frozen, module = "mountainash_rules._native")]
    struct PyDfa {
        inner: Dfa,
    }

    impl PyDfa {
        fn result(py: Python<'_>, value: Result<Dfa, LanguageError>) -> PyResult<Self> {
            value
                .map(|inner| Self { inner })
                .map_err(|error| python_error(py, error))
        }
    }

    #[pymethods]
    impl PyDfa {
        #[staticmethod]
        fn compile(
            py: Python<'_>,
            pattern: &Bound<'_, PyString>,
            flags: (bool, bool, bool, bool, bool, bool, bool),
            limits: PyRef<'_, PyLimits>,
        ) -> PyResult<Self> {
            let limits = limits.inner;
            let pattern = input_text(pattern, limits)?;
            let flags = Flags {
                case_insensitive: flags.0,
                multi_line: flags.1,
                dot_matches_new_line: flags.2,
                crlf: flags.3,
                swap_greed: flags.4,
                unicode: flags.5,
                ignore_whitespace: flags.6,
            };
            Self::result(py, py.detach(|| language::compile(pattern, flags, limits)))
        }

        #[staticmethod]
        fn literal(
            py: Python<'_>,
            text: &Bound<'_, PyString>,
            kind: &str,
            limits: PyRef<'_, PyLimits>,
        ) -> PyResult<Self> {
            let kind = match kind {
                "exact" => LiteralKind::Exact,
                "prefix" => LiteralKind::Prefix,
                "suffix" => LiteralKind::Suffix,
                "contains" => LiteralKind::Contains,
                _ => return Err(PyValueError::new_err("unknown literal language kind")),
            };
            let limits = limits.inner;
            let text = input_text(text, limits)?;
            Self::result(py, py.detach(|| language::literal(text, kind, limits)))
        }

        #[staticmethod]
        fn empty(py: Python<'_>, limits: PyRef<'_, PyLimits>) -> PyResult<Self> {
            let limits = limits.inner;
            Self::result(py, py.detach(|| language::empty(limits)))
        }

        #[staticmethod]
        fn universal(py: Python<'_>, limits: PyRef<'_, PyLimits>) -> PyResult<Self> {
            let limits = limits.inner;
            Self::result(py, py.detach(|| language::universal(limits)))
        }

        #[staticmethod]
        fn from_json(py: Python<'_>, data: &[u8], limits: PyRef<'_, PyLimits>) -> PyResult<Self> {
            let limits = limits.inner;
            Self::result(py, py.detach(|| Dfa::from_json(data, limits)))
        }

        fn accepts(
            &self,
            py: Python<'_>,
            text: &Bound<'_, PyString>,
            limits: PyRef<'_, PyLimits>,
        ) -> PyResult<bool> {
            let limits = limits.inner;
            let text = input_text(text, limits)?;
            py.detach(|| self.inner.accepts(text, limits))
                .map_err(|error| python_error(py, error))
        }

        fn intersection(
            &self,
            py: Python<'_>,
            other: PyRef<'_, PyDfa>,
            limits: PyRef<'_, PyLimits>,
        ) -> PyResult<Self> {
            let limits = limits.inner;
            let other = &other.inner;
            Self::result(py, py.detach(|| self.inner.intersection(other, limits)))
        }

        fn union(
            &self,
            py: Python<'_>,
            other: PyRef<'_, PyDfa>,
            limits: PyRef<'_, PyLimits>,
        ) -> PyResult<Self> {
            let limits = limits.inner;
            let other = &other.inner;
            Self::result(py, py.detach(|| self.inner.union(other, limits)))
        }

        fn difference(
            &self,
            py: Python<'_>,
            other: PyRef<'_, PyDfa>,
            limits: PyRef<'_, PyLimits>,
        ) -> PyResult<Self> {
            let limits = limits.inner;
            let other = &other.inner;
            Self::result(py, py.detach(|| self.inner.difference(other, limits)))
        }

        fn complement(&self, py: Python<'_>, limits: PyRef<'_, PyLimits>) -> PyResult<Self> {
            let limits = limits.inner;
            Self::result(py, py.detach(|| self.inner.complement(limits)))
        }

        fn is_empty(&self, py: Python<'_>, limits: PyRef<'_, PyLimits>) -> PyResult<bool> {
            let limits = limits.inner;
            py.detach(|| self.inner.is_empty(limits))
                .map_err(|error| python_error(py, error))
        }

        fn witness(&self, py: Python<'_>, limits: PyRef<'_, PyLimits>) -> PyResult<Option<String>> {
            let limits = limits.inner;
            py.detach(|| self.inner.witness(limits))
                .map_err(|error| python_error(py, error))
        }

        fn cardinality(
            &self,
            py: Python<'_>,
            bound: usize,
            limits: PyRef<'_, PyLimits>,
        ) -> PyResult<usize> {
            let limits = limits.inner;
            py.detach(|| self.inner.cardinality(bound, limits))
                .map_err(|error| python_error(py, error))
        }

        fn canonical_json<'py>(
            &self,
            py: Python<'py>,
            limits: PyRef<'_, PyLimits>,
        ) -> PyResult<Bound<'py, PyBytes>> {
            let limits = limits.inner;
            let bytes = py
                .detach(|| self.inner.canonical_json(limits))
                .map_err(|error| python_error(py, error))?;
            Ok(PyBytes::new(py, &bytes))
        }
    }

    #[pymodule]
    fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
        let py = module.py();
        module.add_class::<PyLimits>()?;
        module.add_class::<PyDfa>()?;
        for (name, exception) in [
            ("LanguageSyntaxError", py.get_type::<LanguageSyntaxError>()),
            ("LanguageWireError", py.get_type::<LanguageWireError>()),
            (
                "LanguageResourceError",
                py.get_type::<LanguageResourceError>(),
            ),
        ] {
            exception.setattr("__module__", "mountainash_rules._native")?;
            module.add(name, exception)?;
        }
        module.add("REGEX_DIALECT_VERSION", "regex-syntax-0.8.10")?;
        module.add("UNICODE_VERSION", "16.0.0")?;
        Ok(())
    }
}
