import 'package:flutter_test/flutter_test.dart';
import 'package:app/main.dart';

void main() {
  testWidgets('YTM Sync app shell renders', (WidgetTester tester) async {
    await tester.pumpWidget(const YTMSyncApp());
    expect(find.text('YTM SYNC'), findsOneWidget);
    expect(find.text('Dashboard'), findsOneWidget);
  });
}
