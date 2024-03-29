import argparse
import json
import sys
import os
from mutpy import __version__ as version
from mutpy import controller, views, operators, utils


def main(argv):
    parser = build_parser()
    run_mutpy(parser)


def build_parser():
    DEF_TIMEOUT_FACTOR = 5
    parser = argparse.ArgumentParser(description='Mutation testing tool for Python 3.x source code. ',
                                     fromfile_prefix_chars='@')
    parser.add_argument('--version', '-v', action='version', version='%(prog)s {}'.format(version))
    parser.add_argument('--target', '-t', type=str, nargs='+', help='target module or package to mutate')
    parser.add_argument('--unit-test', '-u', type=str, nargs='+',
                        help='test class, test method, module or package with unit tests')
    parser.add_argument('--runner', type=str, choices=['unittest', 'pytest'], default='unittest',
                        metavar='RUNNER', help='test runner')
    parser.add_argument('--report', '-r', type=str, help='generate YAML report', metavar='REPORT_FILE')
    parser.add_argument('--report-html', type=str, help='generate HTML report', metavar='DIR_NAME')
    parser.add_argument('--timeout-factor', '-f', type=float, default=DEF_TIMEOUT_FACTOR,
                        help='max timeout factor (default {})'.format(DEF_TIMEOUT_FACTOR))
    parser.add_argument('--show-mutants', '-m', action='store_true', help='show mutants source code')
    parser.add_argument('--quiet', '-q', action='store_true', help='quiet mode')
    parser.add_argument('--debug', action='store_true', help='debug mode')
    parser.add_argument('--colored-output', '-c', action='store_true', help='try print colored output')
    parser.add_argument('--disable-stdout', '-d', action='store_true',
                        help='try disable stdout during mutation '
                             '(this option can damage your tests if you interact with sys.stdout)')
    parser.add_argument('--experimental-operators', '-e', action='store_true', help='use experimental operators')
    parser.add_argument('--operator', '-o', type=str, nargs='+',
                        help='use only selected operators', metavar='OPERATOR')
    parser.add_argument('--disable-operator', type=str, nargs='+', default=[],
                        help='disable selected operators', metavar='OPERATOR')
    parser.add_argument('--list-operators', '-l', action='store_true', help='list available operators')
    parser.add_argument('--path', '-p', type=str, metavar='DIR', help='extend Python path')
    parser.add_argument('--percentage', type=int, metavar='PERCENTAGE', default=100,
                        help='percentage of the generated mutants (mutation sampling)')
    parser.add_argument('--coverage', action='store_true',
                        help='mutate only covered code')
    parser.add_argument('--order', type=int, metavar='ORDER', default=1, help='mutation order')
    parser.add_argument('--hom-strategy', type=str, metavar='HOM_STRATEGY', help='HOM strategy',
                        default='FIRST_TO_LAST')
    parser.add_argument('--list-hom-strategies', action='store_true', help='list available HOM strategies')
    parser.add_argument('--mutation-number', type=int, metavar='MUTATION_NUMBER',
                        help='run only one mutation (debug purpose)')
    parser.add_argument('--fuzz_shots', type=int, metavar='FUZZ_SHOTS',
                        help='Number of shots want to try for each test per survived mutant while fuzz')
    parser.add_argument('--int_range', type=int, metavar='INT_RANGE', default=1,
                        help='The maximun value that a random integer can have while fuzzing')
    parser.add_argument('--string_range', type=int, metavar='STRING_RANGE', default=2,
                        help='Number of maximun characters a string can have while fuzzing')

    return parser


def run_mutpy(parser):
    cfg = parser.parse_args()
    if cfg.list_operators:
        list_operators()
    elif cfg.list_hom_strategies:
        list_hom_strategies()
    elif cfg.target and cfg.unit_test:
        mutation_controller = build_controller(cfg)
        mutation_controller.run()
        if cfg.fuzz_shots:
            if cfg.runner == 'unittest':
                errors = geterrors()
                start = len(mutation_controller.survived_mutants)
                registry = open("registry.txt", "a")
                registry.write('Number of mutants that survived: ' + str(start))
                registry.write('\n[*] Start Fuzzing...')
                registry.close()
                print('Number of mutants that survived: ' + str(start))
                print('[*] Start Fuzzing...')
                count = 0
                newTests = list(())
                print('Create new inputs for tests')
                for test in cfg.unit_test:
                    count = count + 1
                    newfile = "tmp_" + str(count) + ".py"
                    create_new_test(test, newfile, cfg.fuzz_shots, cfg.int_range, cfg.string_range)
                    newTests.append(newfile)
                test_loader = utils.ModulesLoader(newTests, cfg.path)
                runner_cls = get_runner_cls(cfg.runner)
                mutation_controller.fuzz(test_loader, runner_cls, cfg.coverage, errors)
                for test in newTests:
                    os.remove(test)
                newTests.clear()
                left = len(mutation_controller.survived_mutants)
                print('\n[*] Number of mutants that were killed by the fuzzer: ' + str(start - left))
                print('[*] Number of mutants still alive: ' + str(left))
            else:
                print("The fuzzer option is supported only for Unittest")
    else:
        parser.print_usage()
def geterrors():
    errors = ["input", "Insufficient memory", "BackendNotFoundError", "ValueError", "self.assertTrue(measurement in values)", "self.assertTrue(modinv == expected)", "self.assertListEqual"]
    return errors
def create_new_test(test, newFile, shots, range_int, range_strings):
    fuzzer = controller.FuzzController()
    parts = test.split('.')
    path = '/'.join(parts)
    testFile = path+".py"
    f = open(testFile, "r")
    copy = open(newFile, "w+")
    condition = 0
    lines = []
    for line in f:
        condition = checkLine(line, condition)
        if condition == 1:
            lines.append(line)
        elif condition == 2:
            lines = ''.join(lines)
            newline = fuzzer.create_inputs(lines, shots, range_int, range_strings)
            copy.write(newline)
            copy.write(line)
            lines = []
        else:
            copy.write(line)
    f.close()
    copy.close()

def checkLine(line, condition):
    if "@idata" in line:
        condition = 1
    elif ("@unpack" in line) and (condition == 1):
        condition = 2
    elif condition == 1:
        condition = 1
    else:
        condition = 0
    return condition



def build_controller(cfg):
    runner_cls = get_runner_cls(cfg.runner)
    built_views = build_views(cfg)
    mutant_generator = build_mutator(cfg)
    target_loader = utils.ModulesLoader(cfg.target, cfg.path)
    test_loader = utils.ModulesLoader(cfg.unit_test, cfg.path)
    return controller.MutationController(
        runner_cls=runner_cls,
        target_loader=target_loader,
        test_loader=test_loader,
        views=built_views,
        mutant_generator=mutant_generator,
        timeout_factor=cfg.timeout_factor,
        disable_stdout=cfg.disable_stdout,
        mutate_covered=cfg.coverage,
        mutation_number=cfg.mutation_number
    )


def get_runner_cls(runner):
    if runner == 'unittest':
        from mutpy.test_runners import UnittestTestRunner
        return UnittestTestRunner
    elif runner == 'pytest':
        from mutpy.test_runners import PytestTestRunner
        return PytestTestRunner
    raise ValueError('Unknown runner: {0}'.format(runner))


def build_mutator(cfg):
    operators_set = set()

    if cfg.experimental_operators:
        operators_set |= operators.experimental_operators

    name_to_operator = build_name_to_operator_map()

    if cfg.operator:
        operators_set |= {get_operator(name, name_to_operator)
                          for name in cfg.operator}
    else:
        operators_set |= operators.standard_operators

    operators_set -= {get_operator(name, name_to_operator)
                      for name in cfg.disable_operator}

    if cfg.order == 1:
        return controller.FirstOrderMutator(operators_set, cfg.percentage)
    else:
        hom_strategy = build_hom_strategy(cfg)
        return controller.HighOrderMutator(operators_set, cfg.percentage, hom_strategy=hom_strategy)


def build_hom_strategy(cfg):
    if cfg.order < 1:
        print('Order should be > 0.')
        sys.exit(-1)
    try:
        name_to_hom_strategy = {hom_strategy.name: hom_strategy for hom_strategy in controller.hom_strategies}
        return name_to_hom_strategy[cfg.hom_strategy](order=cfg.order)
    except KeyError:
        print('Unsupported HOM strategy {}! Use --list-hom-strategies to show strategies.'.format(cfg.hom_strategy))
        sys.exit(-1)


def get_operator(name, name_to_operator):
    try:
        return name_to_operator[name]
    except KeyError:
        print('Unsupported operator {}! Use -l to show all operators.'.format(name))
        sys.exit(-1)


def build_name_to_operator_map():
    result = {}
    for operator in operators.standard_operators | operators.experimental_operators:
        result[operator.name()] = operator
        result[operator.long_name()] = operator
    return result


def build_views(cfg):
    views_list = []

    if cfg.quiet:
        views_list.append(views.QuietTextView(cfg.colored_output))
    else:
        views_list.append(views.TextView(cfg.colored_output, cfg.show_mutants))

    if cfg.report:
        views_list.append(views.YAMLReportView(cfg.report))

    if cfg.report_html:
        views_list.append(views.HTMLReportView(cfg.report_html))

    if cfg.debug:
        views_list.append(views.DebugView())

    return views_list


def list_operators():
    print('Standard mutation operators:')
    for operator in utils.sort_operators(operators.standard_operators):
        print(' - {:3} - {}'.format(operator.name(), operator.long_name()))
    print('Experimental mutation operators:')
    for operator in utils.sort_operators(operators.experimental_operators):
        print(' - {:3} - {}'.format(operator.name(), operator.long_name()))


def list_hom_strategies():
    print('HOM strategies:')
    for strategy in controller.hom_strategies:
        print(' - {}'.format(strategy.name))
