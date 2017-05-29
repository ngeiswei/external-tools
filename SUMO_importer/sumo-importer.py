#!/usr/bin/env python2

# Take 2 arguments
#
# 1. the instance to type filename
# 2. sumo filename
#
# and export the file to atomese scheme

import sys
import kifparser
from collections import defaultdict
from opencog.atomspace import AtomSpace, TruthValue, types, get_type

DEFAULT_NODE_TV = TruthValue(0.01, 1)
DEFAULT_LINK_TV = TruthValue(1, 1)
DEFAULT_PREDICATE_TV = TruthValue(0.1, 1)

global atomspace
atomspace=None

def load_instance2type(filename):
    """
    Load a filename generated by sumo-instance-types.py that
    associates each SUMO instance and its atom type and construct a
    dictionary that maps each instance to its atom type.
    """
    i2t = defaultdict(lambda: types.ConceptNode)
    with open(filename, 'rt') as i2tfile:
        for line in i2tfile:
            tokens = line.split()
            instance = tokens[0]
            atom_type = get_type(tokens[1])
            i2t[instance] = atom_type
    return i2t

def convert_multiple_expressions(i2t, expressions):
    for expression in expressions:
        convert_root_expression(i2t, expression)

def is_quantifier(token):
    return token in {"forall", "exists"}

def is_variable(token):
    return token.startswith("?") or token.startswith("@")

def find_free_variables(expression, hidden_variables=None):
    # Work around shitty Python default argument value design
    if hidden_variables is None:
        hidden_variables = set()

    # Base cases
    if isinstance(expression, str):
        if is_variable(expression) and expression not in hidden_variables:
            return [expression]
        return []

    # Recursive cases
    if is_quantifier(expression[0]):
        hidden_variables |= set(expression[1])
        return find_free_variables(expression[2], hidden_variables)
    return set().union(*[find_free_variables(child, hidden_variables)
                         for child in expression])

def convert_root_expression(i2t, expression, link_tv=DEFAULT_LINK_TV):
    """
    Root expressions are implicitely wrapped in a forall. This function
    finds the free variables in the expression and wrap it in a forall
    with the free variable declaration.
    """
    free_variables = find_free_variables(expression)
    if 0 < len(free_variables):
        expression = ["forall", list(free_variables), expression]
    return convert_expression(i2t, expression, link_tv)

def convert_expression(i2t, expression, link_tv=DEFAULT_LINK_TV):
    if isinstance(expression, str):
        return convert_token(i2t, expression)
    else:
        return convert_list(i2t, expression, link_tv)

def convert_token(i2t, token):
    if token.startswith('?') or token.startswith('@'):
        return atomspace.add_node(types.VariableNode, token)
    if token.startswith('"'):
        token = token[1:-2]
    atom_type = i2t[token]
    return atomspace.add_node(atom_type, token, tv=DEFAULT_NODE_TV)

def convert_variable(i2t, variable):
    # This type is gonna work for any translated atom
    predicate_type = atomspace.add_node(types.TypeNode, "PredicateNode")
    schema_type = atomspace.add_node(types.TypeNode, "SchemaNode")
    concept_type = atomspace.add_node(types.TypeNode, "ConceptNode")
    atom_type = atomspace.add_link(types.TypeChoice,
                                   [predicate_type, schema_type, concept_type])
    return atomspace.add_link(types.TypedVariableLink,
                              [convert_token(i2t, variable), atom_type])

def convert_variables(i2t, variables):
    if len(variables) == 1:
        return convert_variable(i2t, variables[0])
    var_atoms = [convert_variable(i2t, variable) for variable in variables]
    return atomspace.add_link(types.VariableList, var_atoms)

def convert_quantifier(i2t, expression, link_tv):
    oper = expression[0]
    args = expression[1:]

    if oper == "forall":
        var_atom = convert_variables(i2t, args[0])

        # (forall (=> is translated into ImplicationScopeLink
        if args[1][0] == "=>":
            args_atoms = [var_atom] + \
                         [convert_expression(i2t, expr, link_tv=None)
                          for expr in args[1][1:]]
            return atomspace.add_link(types.ImplicationScopeLink, args_atoms, link_tv)

        # (forall (<=> is translated into EquivalenceScopeLink
        if args[1][0] == "<=>":
            args_atoms = [var_atom] + \
                         [convert_expression(i2t, expr, link_tv=None)
                          for expr in args[1][1:]]
            return atomspace.add_link(types.EquivalenceScopeLink, args_atoms, link_tv)

        # Otherwise (forall ... is translated into ForAllLink
        args_atoms = [var_atom] + \
                     [convert_expression(i2t, expr, link_tv=None)
                      for expr in args[1:]]
        return atomspace.add_link(types.ForAllLink, args_atoms, link_tv)

    elif oper == "exists":
        var_atom = convert_variables(i2t, args[0])

        # (exists ... is translated into ExistsLink
        args_atoms = [var_atom] + \
                     [convert_expression(i2t, expr, link_tv=None)
                      for expr in args[1:]]
        return atomspace.add_link(types.ExistsLink, args_atoms, link_tv)
    elif oper == "KappaFn":
        var_atom = convert_variable(i2t, args[0]) # Assume only 1 variable

        # (KappaFn ... is translated into SatisfyingSetScopeLink
        args_atoms = [var_atom] + \
                     [convert_expression(i2t, expr, link_tv=None)
                      for expr in args[1:]]
        return atomspace.add_link(types.SatisfyingSetScopeLink, args_atoms, link_tv)
    else:
        return None

def convert_list(i2t, expression, link_tv):
    # First attempt to convert as a quantifier
    quantifier_atom = convert_quantifier(i2t, expression, link_tv)
    if quantifier_atom:
        return quantifier_atom

    # If failed then call default link converter
    oper = expression[0]
    args = expression[1:]
    args_atoms = [convert_expression(i2t, expr, link_tv=None) for expr in args]
    return link(i2t, oper, args_atoms, link_tv)

def link(i2t, oper, args_atoms, link_tv):
    # # If starts with a variable it is a variable list
    # if oper.startswith("?") or oper.startswith("@"):
    #     args_atoms = [convert_token(i2t, oper)] + args_atoms
    #     return atomspace.add_link(types.VariableList, args_atoms, tv=link_tv)

    # Map special operator to Atomese link
    link_type = special_link_type(oper)

    if link_type:
        return atomspace.add_link(link_type, args_atoms, tv=link_tv)

    # Otherwise assume that it is either a schema execution or
    # predicate evaluation
    link_type = i2t[oper]
    node_type = None
    if link_type == types.SchemaNode:
        link_type = types.ExecutionOutputLink
        node_type = types.SchemaNode
    elif link_type == types.PredicateNode:
        link_type = types.EvaluationLink
        node_type = types.PredicateNode
    elif oper.endswith('Fn'):   # This is wrong but may happen if the
                                # SUMO term has not been properly
                                # defined in SUMO. This is here till
                                # it gets fixed on the SUMO side
        link_type = types.ExecutionOutputLink
        node_type = types.SchemaNode
    elif oper.startswith('?') or oper.startswith('?'): # It's a
                                                       # variable,
                                                       # we're gonna
                                                       # assume it is a
                                                       # relation,
                                                       # which seems to
                                                       # be always
                                                       # treated as a
                                                       # predicate
        link_type = types.EvaluationLink
        node_type = types.VariableNode
    else:                       # By default we assume it is a
                                # predicate, which is surely wrong in
                                # practice but seems to work in
                                # practice
        link_type = types.EvaluationLink
        node_type = types.PredicateNode

    node = atomspace.add_node(node_type, oper, tv=DEFAULT_PREDICATE_TV)

    # Wrap in a ListLink if there is more than one argument
    if len(args_atoms) == 1:
        args_atoms = args_atoms[0]
    else:
        args_atoms = atomspace.add_link(types.ListLink, args_atoms)

    return atomspace.add_link(link_type, [node, args_atoms], tv=link_tv)

def special_link_type(oper):
    mapping = {
        '=>':types.ImplicationLink,
        '<=>':types.EquivalenceLink,
        'and':types.AndLink,
        'or':types.OrLink,
        'not':types.NotLink,
        'subclass':types.InheritanceLink,
        'member':types.MemberLink,
        'instance':types.MemberLink,
        'subrelation':types.ImplicationLink,
        'exists':types.ExistsLink,
        'forall':types.ForAllLink,
        'causes':types.ImplicationLink # Should probably be a
                                       # PredictiveImplicationLink but
                                       # that one is not known by the
                                       # python atomspace API
        }

    if oper in mapping:
        return mapping[oper]
    else:
        return None

def print_links(file):
    for atom in atomspace:
        if atom.is_a(types.Link) and atom.tv.count > 0:
            file.write(repr(atom))

def load_sumo(i2t, filename):
    expressions = kifparser.parse_kif_file(filename)
    convert_multiple_expressions(i2t, expressions)

def export_to_scheme(i2f_filename, sumo_filename):
    atomspace.clear()
    i2t = load_instance2type(i2f_filename)
    load_sumo(i2t, sumo_filename)

    output_filename = sumo_filename.replace(".kif.tq", ".scm")
    output_filename = output_filename.replace(".kif", ".scm")
    with open(output_filename, 'w') as out:
        print_links(out)

if __name__ == '__main__':
    atomspace = AtomSpace()
    i2t_filename = sys.argv[1]
    sumo_filename = sys.argv[2]

    export_to_scheme(i2t_filename, sumo_filename)
