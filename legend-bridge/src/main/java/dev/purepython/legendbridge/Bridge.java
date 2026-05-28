package dev.purepython.legendbridge;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.finos.legend.engine.language.pure.grammar.from.PureGrammarParser;
import org.finos.legend.engine.language.pure.grammar.to.PureGrammarComposer;
import org.finos.legend.engine.language.pure.grammar.to.PureGrammarComposerContext;
import org.finos.legend.engine.protocol.pure.v1.PureProtocolObjectMapperFactory;
import org.finos.legend.engine.protocol.pure.v1.model.context.PureModelContextData;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * Thin CLI bridge from pure-python to the real Legend (FINOS) engine.
 *
 * <p>One request per process: the command is argv[0] and the payload is read
 * in full from stdin; the result is written to stdout. A non-zero exit code
 * signals failure, with a diagnostic on stderr.
 *
 * <ul>
 *   <li>{@code parse}   : Pure grammar text (stdin) -&gt; PureModelContextData JSON (stdout)</li>
 *   <li>{@code compose} : PureModelContextData JSON (stdin) -&gt; Pure grammar text (stdout)</li>
 * </ul>
 */
public final class Bridge {
    private static final ObjectMapper MAPPER = PureProtocolObjectMapperFactory.getNewObjectMapper();

    public static void main(String[] args) {
        if (args.length < 1) {
            System.err.println("usage: <parse|compose> ; payload on stdin");
            System.exit(2);
            return;
        }
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
                default:
                    System.err.println("unknown command: " + args[0]);
                    System.exit(2);
                    return;
            }
            System.out.write(output.getBytes(StandardCharsets.UTF_8));
            System.out.flush();
        } catch (Throwable t) {
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

    private Bridge() {
    }
}
