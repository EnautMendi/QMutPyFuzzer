import json
import sys
from mutpy import views, utils, codegen
import random
import string



class TestsFailAtOriginal(Exception):

    def __init__(self, result=None):
        self.result = result


class MutationScore:

    def __init__(self):
        self.killed_mutants = 0
        self.timeout_mutants = 0
        self.incompetent_mutants = 0
        self.survived_mutants = 0
        self.covered_nodes = 0
        self.all_nodes = 0

    def count(self):
        bottom = self.all_mutants - self.incompetent_mutants
        return (((self.killed_mutants + self.timeout_mutants) / bottom) * 100) if bottom else 0

    def inc_killed(self):
        self.killed_mutants += 1

    def inc_timeout(self):
        self.timeout_mutants += 1

    def inc_incompetent(self):
        self.incompetent_mutants += 1

    def inc_survived(self):
        self.survived_mutants += 1

    def update_coverage(self, covered_nodes, all_nodes):
        self.covered_nodes += covered_nodes
        self.all_nodes += all_nodes

    @property
    def all_mutants(self):
        return self.killed_mutants + self.timeout_mutants + self.incompetent_mutants + self.survived_mutants


class MutationController(views.ViewNotifier):

    def __init__(self, runner_cls, target_loader, test_loader, views, mutant_generator,
                 timeout_factor=5, disable_stdout=False, mutate_covered=False, mutation_number=None):
        super().__init__(views)
        self.target_loader = target_loader
        self.test_loader = test_loader
        self.mutant_generator = mutant_generator
        self.timeout_factor = timeout_factor
        self.stdout_manager = utils.StdoutManager(disable_stdout)
        self.mutation_number = mutation_number
        self.survived_mutants = set(())
        self.runner = runner_cls(self.test_loader, self.timeout_factor, self.stdout_manager, mutate_covered)

    def run(self):
        self.notify_initialize(self.target_loader.names, self.test_loader.names)
        try:
            timer = utils.Timer()
            self.run_mutation_process()
            self.notify_end(self.score, timer.stop())
        except TestsFailAtOriginal as error:
            self.notify_original_tests_fail(error.result)
            sys.exit(-1)
        except utils.ModulesLoaderException as error:
            self.notify_cant_load(error.name, error.exception)
            sys.exit(-2)

    def run_mutation_process(self):
        try:
            test_modules, total_duration, number_of_tests = self.load_and_check_tests()
            self.notify_passed(test_modules, number_of_tests)

            self.notify_start()
            self.score = MutationScore()

            for target_module, to_mutate in self.target_loader.load([module for module, *_ in test_modules]):

                self.mutate_module(target_module, to_mutate, total_duration)
        except KeyboardInterrupt:
            pass

    def load_and_check_tests(self):
        test_modules = []
        number_of_tests = 0
        total_duration = 0
        for test_module, target_test in self.test_loader.load():
            result, duration = self.run_test(test_module, target_test)
            if result.was_successful():
                test_modules.append((test_module, target_test, duration))
            else:
                raise TestsFailAtOriginal(result)
            number_of_tests += result.tests_run()
            total_duration += duration

        return test_modules, total_duration, number_of_tests

    def run_test(self, test_module, target_test):
        return self.runner.run_test(test_module, target_test)

    @utils.TimeRegister
    def mutate_module(self, target_module, to_mutate, total_duration):
        target_ast = self.create_target_ast(target_module)
        coverage_injector, coverage_result = self.inject_coverage(target_ast, target_module)
        if coverage_injector:
            self.score.update_coverage(*coverage_injector.get_result())
        for mutations, mutant_ast in self.mutant_generator.mutate(target_ast, to_mutate, coverage_injector,
                                                                  module=target_module):
            mutation_number = self.score.all_mutants + 1
            if self.mutation_number and self.mutation_number != mutation_number:
                self.score.inc_incompetent()
                continue
            self.notify_mutation(mutation_number, mutations, target_module, mutant_ast)
            mutant_module = self.create_mutant_module(target_module, mutant_ast)
            if mutant_module:
                self.run_tests_with_mutant(total_duration, mutant_module, mutations, coverage_result)

            else:
                self.score.inc_incompetent()

    def inject_coverage(self, target_ast, target_module):
        return self.runner.inject_coverage(target_ast, target_module)

    @utils.TimeRegister
    def create_target_ast(self, target_module):
        with open(target_module.__file__) as target_file:
            return utils.create_ast(target_file.read())

    @utils.TimeRegister
    def create_mutant_module(self, target_module, mutant_ast):
        try:
            with self.stdout_manager:
                return utils.create_module(
                    ast_node=mutant_ast,
                    module_name=target_module.__name__
                )
        except BaseException as exception:
            self.notify_incompetent(0, exception, tests_run=0)
            return None

    def run_tests_with_mutant(self, total_duration, mutant_module, mutations, coverage_result):
        result, duration = self.runner.run_tests_with_mutant(total_duration, mutant_module, mutations, coverage_result)

        self.update_score_and_notify_views(result, duration, mutant_module)

    def update_score_and_notify_views(self, result, mutant_duration, mutant_module):
        if not result:
            self.update_timeout_mutant(mutant_duration)
        elif result.is_incompetent:
            self.update_incompetent_mutant(result, mutant_duration)
        elif result.is_survived:
            self.survived_mutants.add(mutant_module)
            self.update_survived_mutant(result, mutant_duration)
        else:
            self.update_killed_mutant(result, mutant_duration)

    def update_timeout_mutant(self, duration):
        self.notify_timeout(duration)
        self.score.inc_timeout()

    def update_incompetent_mutant(self, result, duration):
        self.notify_incompetent(duration, result.exception, result.tests_run)
        self.score.inc_incompetent()

    def update_survived_mutant(self, result, duration):
        self.notify_survived(duration, result.tests_run)
        self.score.inc_survived()

    def update_killed_mutant(self, result, duration):
        self.notify_killed(duration, result.killer, result.exception_traceback, result.tests_run)
        self.score.inc_killed()

    def fuzz(self, test_loader, runner_cls, mutate_covered, errors):
        toremove=list(())
        self.runner = runner_cls(test_loader, self.timeout_factor, self.stdout_manager, mutate_covered)
        for mutant in self.survived_mutants:
            result, duration = self.runner.run_tests_with_mutant_fuzz(100,mutant, errors)
            print('Result for ' + str(mutant) + ' with new inputs:')
            print(result)
            if result:
                if result.is_survived==False:
                    if result.killer:
                        toremove.append(mutant)
            else:
                toremove.append(mutant)
        for x in toremove:
            self.survived_mutants.remove(x)
        pass

class FuzzController():

    def __init__(self):
        super().__init__()
        self.newintegers = list(())
        self.newstrings = list(())
        self.newlists = list(())
        self.newunknown = list(())

    def create_inputs(self, lines, shots, range_int, range_strings):
        splited_lines = lines.split('(')
        splited_lines2 = splited_lines[1].split(')')
        data = splited_lines2[0].replace('\n', '')
        data = data.replace('  ', '')
        data = data.replace(',]', ']')
        try:
            data = json.loads(data)
        except ValueError as e:
            return lines
        newdata = list((data))
        input= data[0]
        if type(input) is list:
            for value in input:
                if type(value) is int:
                    if len(newdata) == len(data):
                        self.newintegers.clear()
                        self.getintegers(shots, range_int)
                        for x in range(shots):
                            newdata.append(list(()))
                            newdata[len(data)+x].append(self.newintegers[x])
                    else:
                        self.newintegers.clear()
                        self.getintegers(shots, range_int)
                        for y in range(len(data), len(newdata)):
                            newdata[y].append(self.newintegers[y - len(data)])
                elif type(value) is str:
                    if len(newdata) == len(data):
                        self.newstrings.clear()
                        self.getstrings(shots, range_strings)
                        for x in range(shots):
                            newdata.append(list(()))
                            newdata[len(data)+x].append(self.newstrings[x])
                    else:
                        self.newstrings.clear()
                        self.getstrings(shots, range_strings)
                        for y in range(len(data), len(newdata)):
                            newdata[y].append(self.newstrings[y - len(data)])
                elif type(value) is list:
                    if len(newdata) == len(data):
                        self.newlists.clear()
                        self.getlists(shots, value, range_int, range_strings)
                        for x in range(shots):
                            newdata.append(list(()))
                            newdata[len(data) + x].append(self.newlists[x])
                    else:
                        self.newlists.clear()
                        self.getlists(shots, value, range_int, range_strings)
                        for y in range(len(data), len(newdata)):
                            newdata[y].append(self.newlists[y - len(data)])
                else:
                    if len(newdata) == len(data):
                        self.newunknown.clear()
                        self.getunknowntype(shots, value)
                        for x in range(shots):
                            newdata.append(list(()))
                            newdata[len(data) + x].append(self.newunknown[x])
                    else:
                        self.newunknown.clear()
                        self.getunknowntype(shots, value)
                        for y in range(len(data), len(newdata)):
                            newdata[y].append(self.newunknown[y - len(data)])
        else:
            return lines
        lines = splited_lines[0] + "(" + str(newdata) + ")\n"

        return lines

    def getintegers(self, shots, range_int):
        for _ in range(shots):
            value = random.randint(0, range_int)
            self.newintegers.append(value)
        pass

    def getstrings(self, shots, range_string):
        for _ in range(shots):
            value = ''.join(random.choices(string.ascii_letters, k=random.randint(0, range_string)))
            self.newstrings.append(value)
        pass

    def getunknowntype(self, shots, seed):
        for _ in range(shots):
            self.newunknown.append(seed)
        pass

    def getlists(self, shots, seed, range_int, range_string):
        for _ in range(shots):
            newlist = list(())
            for x in seed:
                if type(x) is int:
                    value = random.randint(0, range_int)
                elif type(x) is str:
                    value = ''.join(random.choices(string.ascii_letters, k=random.randint(0, range_string)))
                else:
                    value = x
                newlist.append(value)
            self.newlists.append(newlist)
        pass


class HOMStrategy:

    def __init__(self, order=2):
        self.order = order

    def remove_bad_mutations(self, mutations_to_apply, available_mutations, allow_same_operators=True):
        for mutation_to_apply in mutations_to_apply:
            for available_mutation in available_mutations[:]:
                if mutation_to_apply.node == available_mutation.node or \
                        mutation_to_apply.node in available_mutation.node.children or \
                        available_mutation.node in mutation_to_apply.node.children or \
                        (not allow_same_operators and mutation_to_apply.operator == available_mutation.operator):
                    available_mutations.remove(available_mutation)


class FirstToLastHOMStrategy(HOMStrategy):
    name = 'FIRST_TO_LAST'

    def generate(self, mutations):
        mutations = mutations[:]
        while mutations:
            mutations_to_apply = []
            index = 0
            available_mutations = mutations[:]
            while len(mutations_to_apply) < self.order and available_mutations:
                try:
                    mutation = available_mutations.pop(index)
                    mutations_to_apply.append(mutation)
                    mutations.remove(mutation)
                    index = 0 if index == -1 else -1
                except IndexError:
                    break
                self.remove_bad_mutations(mutations_to_apply, available_mutations)
            yield mutations_to_apply


class EachChoiceHOMStrategy(HOMStrategy):
    name = 'EACH_CHOICE'

    def generate(self, mutations):
        mutations = mutations[:]
        while mutations:
            mutations_to_apply = []
            available_mutations = mutations[:]
            while len(mutations_to_apply) < self.order and available_mutations:
                try:
                    mutation = available_mutations.pop(0)
                    mutations_to_apply.append(mutation)
                    mutations.remove(mutation)
                except IndexError:
                    break
                self.remove_bad_mutations(mutations_to_apply, available_mutations)
            yield mutations_to_apply


class BetweenOperatorsHOMStrategy(HOMStrategy):
    name = 'BETWEEN_OPERATORS'

    def generate(self, mutations):
        usage = {mutation: 0 for mutation in mutations}
        not_used = mutations[:]
        while not_used:
            mutations_to_apply = []
            available_mutations = mutations[:]
            available_mutations.sort(key=lambda x: usage[x])
            while len(mutations_to_apply) < self.order and available_mutations:
                mutation = available_mutations.pop(0)
                mutations_to_apply.append(mutation)
                if not usage[mutation]:
                    not_used.remove(mutation)
                usage[mutation] += 1
                self.remove_bad_mutations(mutations_to_apply, available_mutations, allow_same_operators=False)
            yield mutations_to_apply


class RandomHOMStrategy(HOMStrategy):
    name = 'RANDOM'

    def __init__(self, *args, shuffler=random.shuffle, **kwargs):
        super().__init__(*args, **kwargs)
        self.shuffler = shuffler

    def generate(self, mutations):
        mutations = mutations[:]
        self.shuffler(mutations)
        while mutations:
            mutations_to_apply = []
            available_mutations = mutations[:]
            while len(mutations_to_apply) < self.order and available_mutations:
                try:
                    mutation = available_mutations.pop(0)
                    mutations_to_apply.append(mutation)
                    mutations.remove(mutation)
                except IndexError:
                    break
                self.remove_bad_mutations(mutations_to_apply, available_mutations)
            yield mutations_to_apply


hom_strategies = [
    BetweenOperatorsHOMStrategy,
    EachChoiceHOMStrategy,
    FirstToLastHOMStrategy,
    RandomHOMStrategy,
]


class FirstOrderMutator:

    def __init__(self, operators, percentage=100):
        self.operators = operators
        self.sampler = utils.RandomSampler(percentage)

    def mutate(self, target_ast, to_mutate=None, coverage_injector=None, module=None):
        for op in utils.sort_operators(self.operators):
            for mutation, mutant in op().mutate(target_ast, to_mutate, self.sampler, coverage_injector, module=module):
                yield [mutation], mutant


class HighOrderMutator(FirstOrderMutator):

    def __init__(self, *args, hom_strategy=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hom_strategy = hom_strategy or FirstToLastHOMStrategy(order=2)

    def mutate(self, target_ast, to_mutate=None, coverage_injector=None, module=None):
        mutations = self.generate_all_mutations(coverage_injector, module, target_ast, to_mutate)
        for mutations_to_apply in self.hom_strategy.generate(mutations):
            generators = []
            applied_mutations = []
            mutant = target_ast
            for mutation in mutations_to_apply:
                generator = mutation.operator().mutate(
                    mutant,
                    to_mutate=to_mutate,
                    sampler=self.sampler,
                    coverage_injector=coverage_injector,
                    module=module,
                    only_mutation=mutation,
                )
                try:
                    new_mutation, mutant = generator.__next__()
                except StopIteration:
                    assert False, 'no mutations!'
                applied_mutations.append(new_mutation)
                generators.append(generator)
            yield applied_mutations, mutant
            self.finish_generators(generators)

    def generate_all_mutations(self, coverage_injector, module, target_ast, to_mutate):
        mutations = []
        for op in utils.sort_operators(self.operators):
            for mutation, _ in op().mutate(target_ast, to_mutate, None, coverage_injector, module=module):
                mutations.append(mutation)
        return mutations

    def finish_generators(self, generators):
        for generator in reversed(generators):
            try:
                generator.__next__()
            except StopIteration:
                continue
            assert False, 'too many mutations!'
