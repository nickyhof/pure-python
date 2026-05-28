package dev.purepython.legendbridge;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.eclipse.collections.api.RichIterable;
import org.eclipse.collections.api.list.MutableList;
import org.eclipse.collections.impl.factory.Lists;
import org.finos.legend.engine.language.pure.compiler.Compiler;
import org.finos.legend.engine.language.pure.compiler.toPureGraph.HelperValueSpecificationBuilder;
import org.finos.legend.engine.language.pure.compiler.toPureGraph.PureModel;
import org.finos.legend.engine.language.pure.grammar.from.PureGrammarParser;
import org.finos.legend.engine.language.pure.grammar.to.PureGrammarComposer;
import org.finos.legend.engine.language.pure.grammar.to.PureGrammarComposerContext;
import org.finos.legend.engine.plan.execution.PlanExecutor;
import org.finos.legend.engine.plan.execution.result.ConstantResult;
import org.finos.legend.engine.plan.execution.result.Result;
import org.finos.legend.engine.plan.generation.PlanGenerator;
import org.finos.legend.engine.plan.generation.transformers.LegendPlanTransformers;
import org.finos.legend.engine.plan.platform.PlanPlatform;
import org.finos.legend.engine.protocol.pure.v1.PureProtocolObjectMapperFactory;
import org.finos.legend.engine.protocol.pure.v1.model.context.PureModelContextData;
import org.finos.legend.engine.protocol.pure.v1.model.executionPlan.SingleExecutionPlan;
import org.finos.legend.engine.pure.code.core.PureCoreExtensionLoader;
import org.finos.legend.engine.shared.core.deployment.DeploymentMode;
import org.finos.legend.pure.generated.Root_meta_pure_extension_Extension;
import org.finos.legend.pure.m3.coreinstance.meta.pure.metamodel.function.LambdaFunction;
import org.finos.legend.pure.m3.execution.ExecutionSupport;

import java.io.IOException;
import java.io.OutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;

/**
 * Thin CLI bridge from pure-python to the real Legend (FINOS) engine.
 *
 * <p>One request per process: the command is argv[0] and the payload is read in
 * full from stdin; the JSON/text result is written to stdout. A non-zero exit
 * code signals failure, with a diagnostic on stderr.
 *
 * <ul>
 *   <li>{@code parse}   : Pure grammar text -&gt; PureModelContextData JSON</li>
 *   <li>{@code compose} : PureModelContextData JSON -&gt; Pure grammar text</li>
 *   <li>{@code eval}    : {@code {"model": "<pure>", "expression": "|..."}} -&gt;
 *       {@code {"value": ...}} -- compiles and executes the expression via
 *       Legend, delegating evaluation to the engine</li>
 * </ul>
 */
public final class Bridge {
    private static final ObjectMapper MAPPER = PureProtocolObjectMapperFactory.getNewObjectMapper();
    private static final ObjectMapper PLAIN = new ObjectMapper();

    public static void main(String[] args) {
        if (args.length < 1) {
            System.err.println("usage: <parse|compose|eval> ; payload on stdin");
            System.exit(2);
            return;
        }
        // Legend libraries log to stdout during compilation/loading; keep stdout
        // clean for the result by silencing it until we have the final answer.
        PrintStream realOut = System.out;
        System.setOut(new PrintStream(OutputStream.nullOutputStream(), true, StandardCharsets.UTF_8));
        try {
            String input = new String(System.in.readAllBytes(), StandardCharsets.UTF_8);
            String output;
            switch (args[0]) {
                case "parse":
                    output = parse(input);
                    break;
                case "compose":
                    output = compose(input);
                    break;
                case "eval":
                    output = eval(input);
                    break;
                default:
                    System.setOut(realOut);
                    System.err.println("unknown command: " + args[0]);
                    System.exit(2);
                    return;
            }
            System.setOut(realOut);
            realOut.write(output.getBytes(StandardCharsets.UTF_8));
            realOut.flush();
        } catch (Throwable t) {
            System.setOut(realOut);
            System.err.println(t.getClass().getName() + ": " + t.getMessage());
            System.exit(1);
        }
    }

    private static String parse(String pureText) throws IOException {
        PureModelContextData model = PureGrammarParser.newInstance().parseModel(pureText);
        return MAPPER.writeValueAsString(model);
    }

    private static String compose(String json) throws IOException {
        PureModelContextData model = MAPPER.readValue(json, PureModelContextData.class);
        PureGrammarComposer composer = PureGrammarComposer.newInstance(
                PureGrammarComposerContext.Builder.newInstance().build());
        return composer.renderPureModelContextData(model);
    }

    private static String eval(String requestJson) throws IOException {
        JsonNode req = PLAIN.readTree(requestJson);
        String modelText = req.hasNonNull("model") ? req.get("model").asText() : "";
        if (!req.hasNonNull("expression")) {
            throw new IllegalArgumentException("eval request requires an `expression`");
        }
        String expression = req.get("expression").asText();

        PureModelContextData pmcd = PureGrammarParser.newInstance().parseModel(modelText);
        PureModel pureModel = Compiler.compile(pmcd, DeploymentMode.PROD, null);

        LambdaFunction<?> lambda = HelperValueSpecificationBuilder.buildLambda(
                PureGrammarParser.newInstance().parseLambda(expression), pureModel.getContext());

        ExecutionSupport es = pureModel.getExecutionSupport();
        MutableList<Root_meta_pure_extension_Extension> extensions = Lists.mutable.empty();
        for (org.finos.legend.engine.pure.code.core.LegendPureCoreExtension ce : PureCoreExtensionLoader.extensions()) {
            for (Root_meta_pure_extension_Extension e : ce.extraPureCoreExtensions(es)) {
                extensions.add(e);
            }
        }
        // vX_X_X is the version-agnostic protocol handler; concrete versions
        // (e.g. v1_33_0) are not registered in the bundled core platform.
        SingleExecutionPlan plan = PlanGenerator.generateExecutionPlan(
                lambda, null, null, null, pureModel,
                "vX_X_X", PlanPlatform.JAVA, null,
                extensions, LegendPlanTransformers.transformers);

        Result result = PlanExecutor.newPlanExecutorWithAvailableStoreExecutors().execute(plan);
        if (result instanceof ConstantResult) {
            return PLAIN.writeValueAsString(
                    java.util.Collections.singletonMap("value", ((ConstantResult) result).getValue()));
        }
        throw new UnsupportedOperationException(
                "eval supports expressions that reduce to a constant value; got "
                        + result.getClass().getName());
    }

    private Bridge() {
    }
}
